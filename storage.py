# =============================================================================
# AI POWERED FINANCIAL DASHBOARD
# Persistence layer (SQLite).
# =============================================================================

"""Reading and writing the data that backs the dashboard.

The public functions keep the shapes the rest of the app already uses: loaders
return DataFrames with the CSV-era column names, and deletions take a function
that builds a boolean mask from a DataFrame. Only what sits behind them
changed, from CSV files to one SQLite database.

Three properties are enforced here rather than left to the call sites:

1. Writes are atomic. Every change runs in one transaction, so an interrupted
   save leaves the previous state, never a partial one.
2. Read-modify-write cycles are serialized. Writers open their transaction with
   BEGIN IMMEDIATE, which takes SQLite's write lock before the first read. The
   lock lives in the database file, so it holds across threads and across
   processes, unlike the in-process lock the CSV version relied on.
3. Existing CSV history is imported once, verified, and only then retired.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Mapping

import pandas as pd

import backup
from config import (
    CATEGORY_COLUMNS,
    CSV_ENCODING,
    CSV_SEPARATOR,
    PAYMENT_METHOD_COLUMNS,
    TRANSACTION_COLUMNS,
    Config as config,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# CSV-era column name -> SQL column name. The DataFrames the app sees keep the
# first; the database uses the second, which needs no quoting in ad hoc queries.
_TX_COLUMNS: list[tuple[str, str]] = [
    ("Transaction Date", "transaction_date"),
    ("Payment Date", "payment_date"),
    ("Label", "label"),
    ("Category", "category"),
    ("Amount", "amount"),
    ("Installment", "installment"),
    ("Payment Method", "payment_method"),
    ("Hash", "hash"),
    ("Record Timestamp", "record_timestamp"),
    ("Ignore Entry", "ignore_entry"),
]
if [name for name, _ in _TX_COLUMNS] != TRANSACTION_COLUMNS:
    raise RuntimeError("storage._TX_COLUMNS is out of step with config.TRANSACTION_COLUMNS.")

_TX_SELECT = ", ".join(f'{sql} AS "{name}"' for name, sql in _TX_COLUMNS)
_TX_INSERT = (
    f"INSERT INTO transactions ({', '.join(sql for _, sql in _TX_COLUMNS)}) "
    f"VALUES ({', '.join('?' for _ in _TX_COLUMNS)})"
)

# One statement per entry. They run one by one inside the bootstrap
# transaction, because executescript() would commit it implicitly.
_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS transactions (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        transaction_date TEXT,
        payment_date     TEXT,
        label            TEXT,
        category         TEXT,
        amount           REAL    NOT NULL,
        installment      TEXT,
        payment_method   TEXT,
        hash             TEXT    NOT NULL,
        record_timestamp TEXT,
        ignore_entry     INTEGER NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_transactions_hash ON transactions (hash)",
    "CREATE TABLE IF NOT EXISTS categories (name TEXT PRIMARY KEY)",
    """
    CREATE TABLE IF NOT EXISTS payment_methods (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        name         TEXT NOT NULL UNIQUE,
        close_date   INTEGER,
        payment_date INTEGER,
        type         TEXT NOT NULL
    )
    """,
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)",
)


class DataIntegrityError(RuntimeError):
    """Raised when stored data does not match what the app relies on."""


# -----------------------------------------------------------------------------
# Connections and transactions
# -----------------------------------------------------------------------------

def _connect() -> sqlite3.Connection:
    """Open a short-lived connection in autocommit mode.

    One connection per operation keeps every thread on its own handle (a
    sqlite3 connection must not be shared across threads) and costs well under
    a millisecond. The busy timeout makes a writer wait for another writer
    instead of failing at once.
    """
    connection = sqlite3.connect(config.db_path, timeout=30, isolation_level=None)
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


@contextmanager
def _transaction() -> Iterator[sqlite3.Connection]:
    """One write transaction: commit on success, roll back on any exception."""
    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.execute("COMMIT")
    except BaseException:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        connection.close()


def _clean(value):
    """Turn a DataFrame/dict cell into something sqlite3 can bind.

    NaN and empty strings become NULL, matching how the CSV reader treated an
    empty field. NumPy scalars are unwrapped to plain Python numbers.
    """
    if value is None:
        return None
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        value = value.item()
    if isinstance(value, float) and value != value:
        return None
    if isinstance(value, str) and value == "":
        return None
    return value


def _tx_params(record: Mapping) -> tuple:
    """One transaction record, in INSERT column order, with Amount/Ignore typed."""
    values = []
    for name, _ in _TX_COLUMNS:
        value = _clean(record.get(name))
        if name == "Amount":
            if value is None:
                raise ValueError("A transaction needs an Amount.")
            value = float(value)
        elif name == "Ignore Entry":
            value = int(value or 0)
        elif name == "Hash" and value is None:
            raise ValueError("A transaction needs a Hash.")
        values.append(value)
    return tuple(values)


def _day_or_none(value) -> int | None:
    """A payment-method day as an int, or None when blank or not a number."""
    value = _clean(value)
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


# -----------------------------------------------------------------------------
# Bootstrap and one-time CSV import
# -----------------------------------------------------------------------------

def _validate_columns(df: pd.DataFrame, expected: list[str], path: Path) -> None:
    """Fail loudly when a legacy CSV is present but missing columns."""
    missing = [column for column in expected if column not in df.columns]
    if missing:
        raise DataIntegrityError(
            f"{path.name} is missing required column(s): {', '.join(missing)}. "
            f"Expected header: {CSV_SEPARATOR.join(expected)}"
        )


def _read_legacy_csv(path: Path, columns: list[str]) -> pd.DataFrame | None:
    """Read a legacy CSV as text, so nothing is reinterpreted on the way in."""
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path, sep=CSV_SEPARATOR, encoding=CSV_ENCODING, dtype=str)
    except pd.errors.EmptyDataError:
        return None
    if df.empty:
        return None
    _validate_columns(df, columns, path)
    return df


def _meta_get(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _meta_set(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def _import_legacy_csv(connection: sqlite3.Connection) -> list[Path]:
    """Copy the old CSV files into the (empty) tables. Returns the files used.

    Runs inside the caller's transaction. Rows are inserted, then counted and
    summed back out of the database and compared with what was read; any
    difference raises, which rolls the whole import back and stops start-up
    instead of letting the app run on a partial copy of the history.
    """
    used: list[Path] = []

    transactions = _read_legacy_csv(config.csv_db, TRANSACTION_COLUMNS)
    if transactions is not None:
        transactions = transactions.copy()
        transactions["Amount"] = pd.to_numeric(transactions["Amount"], errors="coerce")
        skipped = int(transactions["Amount"].isna().sum())
        if skipped:
            logger.warning(
                "Import: skipping %d row(s) in %s with a non-numeric Amount "
                "(the app already ignored them). The file is kept for recovery.",
                skipped, config.csv_db.name,
            )
            transactions = transactions.dropna(subset=["Amount"])
        transactions["Ignore Entry"] = (
            pd.to_numeric(transactions["Ignore Entry"], errors="coerce").fillna(0).astype(int)
        )
        records = transactions.to_dict("records")
        connection.executemany(_TX_INSERT, [_tx_params(r) for r in records])

        count, total = connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM transactions"
        ).fetchone()
        expected_total = float(transactions["Amount"].sum())
        if count != len(records) or abs(total - expected_total) > 1e-6:
            raise DataIntegrityError(
                f"CSV import check failed: read {len(records)} row(s) summing to "
                f"{expected_total:.2f}, database holds {count} summing to {total:.2f}."
            )
        used.append(config.csv_db)
        logger.info("Imported %d transaction(s) from %s.", count, config.csv_db.name)

    categories = _read_legacy_csv(config.categories_db, CATEGORY_COLUMNS)
    if categories is not None:
        names = (
            categories["Name"].dropna().astype(str).str.strip()
            .loc[lambda s: s.ne("") & s.ne("nan")].drop_duplicates()
        )
        connection.executemany("INSERT INTO categories (name) VALUES (?)", [(n,) for n in names])
        used.append(config.categories_db)
        logger.info("Imported %d categorie(s).", len(names))

    methods = _read_legacy_csv(config.payment_methods_db, PAYMENT_METHOD_COLUMNS)
    if methods is not None:
        methods = methods.dropna(subset=["Name", "Type"])
        methods = methods[methods["Name"].str.strip().ne("") & methods["Type"].str.strip().ne("")]
        # The app keys methods by name, so a repeated name was already
        # resolved to its last row; keep that outcome rather than fail on the
        # UNIQUE constraint.
        deduplicated = methods.drop_duplicates(subset=["Name"], keep="last")
        if len(deduplicated) != len(methods):
            logger.warning(
                "Import: %d payment method(s) repeated a name; kept the last of each.",
                len(methods) - len(deduplicated),
            )
        connection.executemany(
            "INSERT INTO payment_methods (name, close_date, payment_date, type) VALUES (?, ?, ?, ?)",
            [
                (r["Name"].strip(), _day_or_none(r["Close Date"]),
                 _day_or_none(r["Payment Date"]), r["Type"].strip())
                for r in deduplicated.to_dict("records")
            ],
        )
        used.append(config.payment_methods_db)
        logger.info("Imported %d payment method(s).", len(deduplicated))

    return used


def bootstrap_data_files() -> None:
    """Create the database if needed, import legacy CSVs once, check integrity.

    The name is kept from the CSV era so callers need no change.
    """
    config.data_dir.mkdir(parents=True, exist_ok=True)

    # Must run outside a transaction. WAL lets readers and the writer proceed
    # together, and is remembered in the database file from then on.
    setup = _connect()
    try:
        setup.execute("PRAGMA journal_mode = WAL")
    finally:
        setup.close()

    retired: list[Path] = []
    with _transaction() as connection:
        for statement in _SCHEMA_STATEMENTS:
            connection.execute(statement)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

        if _meta_get(connection, "legacy_csv_imported") is None:
            if connection.execute("SELECT 1 FROM transactions LIMIT 1").fetchone():
                logger.warning("Tables already hold data; skipping the legacy CSV import.")
            else:
                retired = _import_legacy_csv(connection)
            _meta_set(connection, "legacy_csv_imported", "1")

    # Only after the import has committed. Renaming (not deleting) keeps the
    # originals, and stops a later wipe of the database from silently bringing
    # an out-of-date copy of the history back.
    for path in retired:
        target = path.with_name(path.name + ".migrated")
        try:
            path.rename(target)
            logger.info("Legacy file %s renamed to %s.", path.name, target.name)
        except OSError as exc:
            logger.warning("Could not rename %s after importing it: %s", path.name, exc)

    verify = _connect()
    try:
        verdict = verify.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        verify.close()
    if verdict != "ok":
        raise DataIntegrityError(
            f"The database failed its integrity check ({verdict}). Restore a snapshot with "
            f"`python backup.py list` and `python backup.py restore <file>`."
        )


# -----------------------------------------------------------------------------
# Transactions
# -----------------------------------------------------------------------------

def _read_transactions(connection: sqlite3.Connection) -> pd.DataFrame:
    """The whole table, indexed by row id so a mask can be turned into deletions."""
    df = pd.read_sql_query(
        f"SELECT id, {_TX_SELECT} FROM transactions ORDER BY id", connection, index_col="id"
    )
    if df.empty:
        return pd.DataFrame(columns=TRANSACTION_COLUMNS)
    df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce")
    df["Ignore Entry"] = pd.to_numeric(df["Ignore Entry"], errors="coerce").fillna(0).astype(int)
    return df


def load_transactions() -> pd.DataFrame:
    """Load the full transactions table."""
    connection = _connect()
    try:
        df = _read_transactions(connection)
    finally:
        connection.close()
    return df.reset_index(drop=True)


def save_transactions(df: pd.DataFrame) -> None:
    """Replace the entire transactions table with `df`."""
    params = [_tx_params(r) for r in df.reindex(columns=TRANSACTION_COLUMNS).to_dict("records")]
    with _transaction() as connection:
        connection.execute("DELETE FROM transactions")
        connection.executemany(_TX_INSERT, params)


def append_transactions(records: list[dict]) -> None:
    """Append new records in one transaction."""
    if not records:
        return
    params = [_tx_params(r) for r in records]
    with _transaction() as connection:
        connection.executemany(_TX_INSERT, params)


def _matching_ids(df: pd.DataFrame, mask_fn) -> list[int]:
    """Row ids picked by `mask_fn`, matched by position so any index works."""
    if df.empty:
        return []
    mask = pd.Series(mask_fn(df)).to_numpy(dtype=bool)
    if len(mask) != len(df):
        raise ValueError("The deletion mask must have one entry per row.")
    return [int(i) for i in df.index[mask]]


def delete_transactions(mask_fn) -> int:
    """Delete rows for which `mask_fn(df)` is True. Returns the number removed.

    `mask_fn` receives the freshly loaded DataFrame and must return a boolean
    Series. Reading inside the write transaction means the deletion always
    applies to current data, even if another callback wrote between the user's
    click and this call.
    """
    backup.snapshot_before_change()
    with _transaction() as connection:
        ids = _matching_ids(_read_transactions(connection), mask_fn)
        connection.executemany("DELETE FROM transactions WHERE id = ?", [(i,) for i in ids])
        return len(ids)


def replace_transactions(mask_fn, new_records: list[dict]) -> int:
    """Delete rows matching `mask_fn` and append `new_records`, atomically.

    Used to edit an existing purchase: the caller regenerates the full
    installment schedule from the edited fields and this swaps it in for
    whatever currently matches `mask_fn` (typically "every row with this
    Hash"). Both steps share one transaction, so no other write can land
    between them and no reader ever sees the purchase half-replaced.
    """
    params = [_tx_params(r) for r in new_records]
    backup.snapshot_before_change()
    with _transaction() as connection:
        ids = _matching_ids(_read_transactions(connection), mask_fn)
        connection.executemany("DELETE FROM transactions WHERE id = ?", [(i,) for i in ids])
        connection.executemany(_TX_INSERT, params)
        return len(ids)


# -----------------------------------------------------------------------------
# Categories
# -----------------------------------------------------------------------------

def load_categories() -> pd.DataFrame:
    """Load the category list, sorted alphabetically."""
    connection = _connect()
    try:
        df = pd.read_sql_query('SELECT name AS "Name" FROM categories ORDER BY name', connection)
    finally:
        connection.close()
    if df.empty:
        return pd.DataFrame(columns=CATEGORY_COLUMNS)
    return df


def get_categories() -> list[str]:
    """Return category names as a sorted list of strings."""
    df = load_categories()
    if df.empty:
        return []
    return sorted(df["Name"].dropna().astype(str).tolist())


def save_categories(df: pd.DataFrame) -> None:
    """Persist categories, dropping blanks and de-duplicating case-sensitively."""
    cleaned = df.reindex(columns=CATEGORY_COLUMNS).copy()
    cleaned["Name"] = cleaned["Name"].astype(str).str.strip()
    cleaned = cleaned[cleaned["Name"].ne("") & cleaned["Name"].ne("nan")]
    cleaned = cleaned.drop_duplicates(subset=["Name"]).sort_values("Name")
    with _transaction() as connection:
        connection.execute("DELETE FROM categories")
        connection.executemany(
            "INSERT INTO categories (name) VALUES (?)", [(n,) for n in cleaned["Name"]]
        )


# -----------------------------------------------------------------------------
# Payment methods
# -----------------------------------------------------------------------------

def load_payment_methods() -> pd.DataFrame:
    """Load payment methods sorted by name, then by type descending."""
    connection = _connect()
    try:
        df = pd.read_sql_query(
            'SELECT name AS "Name", close_date AS "Close Date", '
            'payment_date AS "Payment Date", type AS "Type" FROM payment_methods',
            connection,
        )
    finally:
        connection.close()
    if df.empty:
        return pd.DataFrame(columns=PAYMENT_METHOD_COLUMNS)
    return df.sort_values(by=["Name", "Type"], ascending=[True, False])


def save_payment_methods(df: pd.DataFrame) -> None:
    """Persist payment methods, dropping rows with an empty Name or Type."""
    cleaned = df.reindex(columns=PAYMENT_METHOD_COLUMNS).copy()
    cleaned = cleaned[cleaned["Name"].astype(str).str.strip().ne("")]
    cleaned = cleaned[cleaned["Type"].astype(str).str.strip().ne("")]
    params = [
        (
            str(r["Name"]), _day_or_none(r["Close Date"]),
            _day_or_none(r["Payment Date"]), str(r["Type"]).strip(),
        )
        for r in cleaned.to_dict("records")
    ]
    with _transaction() as connection:
        connection.execute("DELETE FROM payment_methods")
        connection.executemany(
            "INSERT INTO payment_methods (name, close_date, payment_date, type) VALUES (?, ?, ?, ?)",
            params,
        )


def get_payment_methods() -> dict[str, dict]:
    """Return every payment method keyed by name.

    Credit methods carry a statement close day and a payment day; debit methods
    legitimately leave both empty, so missing values are normalized to None
    rather than raising.
    """
    df = load_payment_methods()
    methods: dict[str, dict] = {}
    for _, row in df.iterrows():
        try:
            close_day = int(row["Close Date"]) if pd.notna(row["Close Date"]) else None
            pay_day = int(row["Payment Date"]) if pd.notna(row["Payment Date"]) else None
        except (TypeError, ValueError):
            close_day, pay_day = None, None
        methods[str(row["Name"])] = {
            "statement_close_day": close_day,
            "payment_day": pay_day,
            "type": str(row["Type"]).strip(),
        }
    return methods


def get_payment_method(name: str) -> dict:
    """Return a single payment method, with a clear error if it is unknown."""
    methods = get_payment_methods()
    if name not in methods:
        raise KeyError(f"Unknown payment method: {name!r}")
    return methods[name]


# -----------------------------------------------------------------------------
# CSV export
# -----------------------------------------------------------------------------

def export_csv(directory: Path) -> list[Path]:
    """Write the three tables as CSV files, for Excel, pandas or a hand check.

    One-way on purpose: the database is the source of truth, and edits made to
    an exported file are not read back.
    """
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, frame in (
        ("data.csv", load_transactions()),
        ("categories.csv", load_categories()),
        ("payment_methods.csv", load_payment_methods()),
    ):
        path = directory / name
        frame.to_csv(path, sep=CSV_SEPARATOR, index=False, encoding=CSV_ENCODING)
        written.append(path)
    return written


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "export":
        for exported in export_csv(Path(sys.argv[2])):
            print(exported)
    else:
        print("Usage: python storage.py export <directory>")
        sys.exit(2)