# =============================================================================
# AI POWERED FINANCIAL DASHBOARD
# CSV persistence layer.
# =============================================================================

"""Reading and writing the CSV files that back the dashboard.

Two properties matter here and are enforced in this module rather than by
convention at the call sites:

1. Writes are atomic. Dash callbacks can overlap, and a partially written CSV
   would silently corrupt the user's financial history. Every write goes to a
   temporary file in the same directory and is then moved into place, which is
   an atomic operation on POSIX filesystems.
2. Writes are serialized. A process-wide lock prevents two concurrent callbacks
   from interleaving a read-modify-write cycle and losing one of the updates.
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
from pathlib import Path

import pandas as pd

from config import (
    CATEGORY_COLUMNS,
    CSV_ENCODING,
    CSV_SEPARATOR,
    PAYMENT_METHOD_COLUMNS,
    TRANSACTION_COLUMNS,
    Config as config,
)

logger = logging.getLogger(__name__)

# Serializes read-modify-write cycles across Dash callback threads.
_WRITE_LOCK = threading.Lock()


class DataIntegrityError(RuntimeError):
    """Raised when a CSV file exists but does not match the expected schema."""


# -----------------------------------------------------------------------------
# Bootstrap
# -----------------------------------------------------------------------------

def _write_atomic(df: pd.DataFrame, path: Path) -> None:
    """Write `df` to `path` atomically, preserving the previous file on failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=CSV_ENCODING, newline="") as handle:
            df.to_csv(handle, sep=CSV_SEPARATOR, index=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _ensure_file(path: Path, columns: list[str]) -> None:
    """Create `path` with a header row if it does not exist yet."""
    if path.exists():
        return
    logger.info("Creating missing data file with empty schema: %s", path)
    _write_atomic(pd.DataFrame(columns=columns), path)


def _validate_columns(df: pd.DataFrame, expected: list[str], path: Path) -> None:
    """Fail loudly when a file is present but missing columns the app relies on."""
    missing = [column for column in expected if column not in df.columns]
    if missing:
        raise DataIntegrityError(
            f"{path.name} is missing required column(s): {', '.join(missing)}. "
            f"Expected header: {CSV_SEPARATOR.join(expected)}"
        )


def bootstrap_data_files() -> None:
    """Create any missing CSV files so a fresh checkout starts without errors."""
    config.data_dir.mkdir(parents=True, exist_ok=True)
    _ensure_file(config.csv_db, TRANSACTION_COLUMNS)
    _ensure_file(config.categories_db, CATEGORY_COLUMNS)
    _ensure_file(config.payment_methods_db, PAYMENT_METHOD_COLUMNS)


# -----------------------------------------------------------------------------
# Transactions
# -----------------------------------------------------------------------------

def load_transactions() -> pd.DataFrame:
    """Load the full transactions table, normalizing dtypes for safe arithmetic."""
    try:
        df = pd.read_csv(config.csv_db, sep=CSV_SEPARATOR, encoding=CSV_ENCODING)
    except pd.errors.EmptyDataError:
        logger.warning("%s is empty; returning an empty table.", config.csv_db)
        return pd.DataFrame(columns=TRANSACTION_COLUMNS)

    if df.empty:
        return pd.DataFrame(columns=TRANSACTION_COLUMNS)

    _validate_columns(df, TRANSACTION_COLUMNS, config.csv_db)

    # Coerce the two columns the aggregations depend on. Rows with an unparseable
    # Amount are dropped rather than silently poisoning every sum downstream.
    df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce")
    invalid_amounts = int(df["Amount"].isna().sum())
    if invalid_amounts:
        logger.warning("Dropping %d row(s) with a non-numeric Amount.", invalid_amounts)
        df = df.dropna(subset=["Amount"])

    df["Ignore Entry"] = pd.to_numeric(df["Ignore Entry"], errors="coerce").fillna(0).astype(int)
    return df


def save_transactions(df: pd.DataFrame) -> None:
    """Persist the transactions table, keeping the canonical column order."""
    ordered = df.reindex(columns=TRANSACTION_COLUMNS)
    _write_atomic(ordered, config.csv_db)


def append_transactions(records: list[dict]) -> None:
    """Append new records under the write lock, re-reading to avoid lost updates."""
    if not records:
        return
    with _WRITE_LOCK:
        current = load_transactions()
        updated = pd.concat([current, pd.DataFrame(records)], ignore_index=True)
        save_transactions(updated)


def delete_transactions(mask_fn) -> int:
    """Delete rows for which `mask_fn(df)` is True. Returns the number removed.

    `mask_fn` receives the freshly loaded DataFrame and must return a boolean
    Series. Reading inside the lock means the deletion always applies to current
    data, even if another callback wrote between the user's click and this call.
    """
    with _WRITE_LOCK:
        current = load_transactions()
        if current.empty:
            return 0
        mask = mask_fn(current)
        removed = int(mask.sum())
        if removed:
            save_transactions(current[~mask])
        return removed


# -----------------------------------------------------------------------------
# Categories
# -----------------------------------------------------------------------------

def load_categories() -> pd.DataFrame:
    """Load the category list, sorted alphabetically."""
    try:
        df = pd.read_csv(config.categories_db, sep=CSV_SEPARATOR, encoding=CSV_ENCODING)
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=CATEGORY_COLUMNS)

    if df.empty:
        return pd.DataFrame(columns=CATEGORY_COLUMNS)

    _validate_columns(df, CATEGORY_COLUMNS, config.categories_db)
    return df.sort_values(by=["Name"], ascending=[True])


def get_categories() -> list[str]:
    """Return category names as a sorted list of strings."""
    df = load_categories()
    if df.empty:
        return []
    return sorted(df["Name"].dropna().astype(str).tolist())


# -----------------------------------------------------------------------------
# Payment methods
# -----------------------------------------------------------------------------

def load_payment_methods() -> pd.DataFrame:
    """Load payment methods sorted by name, then by type descending."""
    try:
        df = pd.read_csv(config.payment_methods_db, sep=CSV_SEPARATOR, encoding=CSV_ENCODING)
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=PAYMENT_METHOD_COLUMNS)

    if df.empty:
        return pd.DataFrame(columns=PAYMENT_METHOD_COLUMNS)

    _validate_columns(df, PAYMENT_METHOD_COLUMNS, config.payment_methods_db)
    return df.sort_values(by=["Name", "Type"], ascending=[True, False])


def save_payment_methods(df: pd.DataFrame) -> None:
    """Persist payment methods, dropping rows with an empty Name or Type."""
    cleaned = df.reindex(columns=PAYMENT_METHOD_COLUMNS).copy()
    cleaned = cleaned[cleaned["Name"].astype(str).str.strip().ne("")]
    cleaned = cleaned[cleaned["Type"].astype(str).str.strip().ne("")]
    with _WRITE_LOCK:
        _write_atomic(cleaned, config.payment_methods_db)


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
