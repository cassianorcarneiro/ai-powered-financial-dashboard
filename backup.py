# =============================================================================
# AI POWERED FINANCIAL DASHBOARD
# Database snapshots: scheduled, pre-change, retention and restore.
# =============================================================================

"""Consistent snapshots of the SQLite database.

Two kinds of snapshot exist, because they answer different mistakes:

- "scheduled": one per BACKUP_INTERVAL_HOURS, taken at start-up and then by a
  background thread. Recovers from anything noticed days later.
- "safety": taken right before a delete or an edit, throttled to one per
  BACKUP_SAFETY_MIN_MINUTES. Recovers from a mistake made minutes after the
  last scheduled snapshot, which a daily schedule alone cannot.

Each kind is pruned independently, so a burst of safety snapshots can never
push the scheduled history out of the retention window.

A snapshot is made with SQLite's own online backup API, not by copying the
file. Copying a database in WAL mode while it is being written can capture the
main file and the write-ahead log at different moments and produce a file that
does not open. The backup API reads one consistent state, and it works while
the app keeps serving requests.

Command line (run with the app stopped when restoring):

    python backup.py list
    python backup.py create
    python backup.py restore <file name or path>
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path

from config import Config as config

logger = logging.getLogger(__name__)

SCHEDULED = "scheduled"
SAFETY = "safety"
_KINDS = (SCHEDULED, SAFETY)

_STAMP_FORMAT = "%Y%m%d-%H%M%S"
_NAME_RE = re.compile(r"^dashboard-(scheduled|safety)-(\d{8}-\d{6})(?:-(\d+))?\.db$")

# Serializes snapshot creation inside this process, so two callbacks that both
# find a snapshot "due" do not both take one.
_CREATE_LOCK = threading.Lock()
_scheduler_started = False


# -----------------------------------------------------------------------------
# Listing and retention
# -----------------------------------------------------------------------------

def _parse(path: Path) -> tuple[str, datetime, int] | None:
    """Return (kind, timestamp, same-second counter) for a snapshot file name.

    The counter is the "-1", "-2" suffix given to snapshots taken within the
    same second; it keeps their order correct, since comparing the file names
    as text would put "...-1.db" before "....db".
    """
    match = _NAME_RE.match(path.name)
    if not match:
        return None
    return match.group(1), datetime.strptime(match.group(2), _STAMP_FORMAT), int(match.group(3) or 0)


def list_backups(kind: str | None = None) -> list[Path]:
    """Snapshots on disk, newest first, optionally of one kind only."""
    directory = config.backup_dir
    if not directory.is_dir():
        return []
    found = []
    for path in directory.iterdir():
        parsed = _parse(path)
        if parsed and (kind is None or parsed[0] == kind):
            found.append(((parsed[1], parsed[2]), path))
    found.sort(key=lambda item: item[0], reverse=True)
    return [path for _, path in found]


def _newest_timestamp(kind: str) -> datetime | None:
    backups = list_backups(kind)
    return _parse(backups[0])[1] if backups else None


def _prune(kind: str) -> None:
    keep = config.backup_keep_scheduled if kind == SCHEDULED else config.backup_keep_safety
    for stale in list_backups(kind)[max(keep, 1):]:
        try:
            stale.unlink()
            logger.info("Removed old %s backup %s.", kind, stale.name)
        except OSError as exc:
            logger.warning("Could not remove old backup %s: %s", stale.name, exc)


# -----------------------------------------------------------------------------
# Creating snapshots
# -----------------------------------------------------------------------------

def create_backup(kind: str = SCHEDULED) -> Path | None:
    """Take one snapshot now. Returns its path, or None when there is no database yet."""
    if kind not in _KINDS:
        raise ValueError(f"Unknown backup kind: {kind!r}")
    if not config.db_path.exists():
        return None

    with _CREATE_LOCK:
        directory = config.backup_dir
        directory.mkdir(parents=True, exist_ok=True)

        # Two snapshots in the same second get a "-N" suffix. N continues from
        # the highest one already on disk rather than from the first free
        # name: pruning can remove the lowest and leave a gap, and reusing
        # that gap would give the newest snapshot the oldest-looking name.
        now = datetime.now().replace(microsecond=0)
        same_second = [
            parsed[2] for parsed in map(_parse, list_backups(kind)) if parsed and parsed[1] == now
        ]
        suffix = f"-{max(same_second) + 1}" if same_second else ""
        final = directory / f"dashboard-{kind}-{now.strftime(_STAMP_FORMAT)}{suffix}.db"

        # Written under a temporary name and moved into place only once it has
        # been checked, so a half-written or damaged file never looks like a
        # usable backup.
        fd, tmp_name = tempfile.mkstemp(dir=str(directory), prefix=".dashboard-", suffix=".tmp")
        os.close(fd)
        tmp = Path(tmp_name)
        source = sqlite3.connect(config.db_path, timeout=30)
        destination = sqlite3.connect(tmp)
        try:
            source.backup(destination)
            verdict = destination.execute("PRAGMA quick_check").fetchone()[0]
            if verdict != "ok":
                raise sqlite3.DatabaseError(f"quick_check on the new snapshot said: {verdict}")
        except Exception:
            destination.close()
            source.close()
            tmp.unlink(missing_ok=True)
            raise
        destination.close()
        source.close()

        os.replace(tmp, final)
        logger.info("Created %s backup %s (%d KB).", kind, final.name, final.stat().st_size // 1024)

    _prune(kind)
    return final


def snapshot_if_due(kind: str, min_age: timedelta) -> Path | None:
    """Take a snapshot unless one of this kind is younger than `min_age`.

    A failure here is logged and swallowed. A backup problem (disk full, bad
    permissions) must not stop the user's own save or delete from working, but
    it must not be silent either, so it is logged at ERROR.
    """
    try:
        newest = _newest_timestamp(kind)
        if newest is not None and datetime.now() - newest < min_age:
            return None
        return create_backup(kind)
    except Exception:
        logger.exception("Could not create a %s backup.", kind)
        return None


def snapshot_before_change() -> Path | None:
    """The throttled safety snapshot taken before a delete or an edit."""
    return snapshot_if_due(SAFETY, timedelta(minutes=config.backup_safety_min_minutes))


def snapshot_if_scheduled_due() -> Path | None:
    return snapshot_if_due(SCHEDULED, timedelta(hours=config.backup_interval_hours))


# -----------------------------------------------------------------------------
# Scheduler
# -----------------------------------------------------------------------------

def start_scheduler() -> None:
    """Take a scheduled snapshot now if due, then keep checking in the background.

    The thread is a daemon, so it never keeps the process alive. It wakes every
    half hour and the "is one due" test reads the snapshot file names, which
    makes it safe if more than one process runs this: the loser of a race finds
    a fresh snapshot already there.
    """
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    snapshot_if_scheduled_due()

    def _loop() -> None:
        stop = threading.Event()
        while not stop.wait(30 * 60):
            snapshot_if_scheduled_due()

    threading.Thread(target=_loop, name="backup-scheduler", daemon=True).start()
    logger.info(
        "Backups: every %dh (keep %d), plus a safety snapshot before changes "
        "(at most one per %d min, keep %d), in %s.",
        config.backup_interval_hours, config.backup_keep_scheduled,
        config.backup_safety_min_minutes, config.backup_keep_safety, config.backup_dir,
    )


# -----------------------------------------------------------------------------
# Restore
# -----------------------------------------------------------------------------

def restore_backup(name_or_path: str) -> Path:
    """Replace the live database with a snapshot. The app must be stopped.

    The current database is itself snapshotted first (as a safety backup), so a
    restore of the wrong file can be undone. The old -wal/-shm companions are
    removed: left behind, SQLite would replay them onto the restored file.
    """
    candidate = Path(name_or_path)
    if not candidate.exists():
        candidate = config.backup_dir / name_or_path
    if not candidate.exists():
        raise FileNotFoundError(f"No such backup: {name_or_path}")

    check = sqlite3.connect(f"file:{candidate}?mode=ro", uri=True)
    try:
        verdict = check.execute("PRAGMA quick_check").fetchone()[0]
        has_table = check.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='transactions'"
        ).fetchone()
    except sqlite3.DatabaseError as exc:
        verdict, has_table = str(exc), None
    finally:
        check.close()
    if verdict != "ok" or not has_table:
        raise ValueError(f"{candidate.name} is not a usable database snapshot ({verdict}).")

    if config.db_path.exists():
        create_backup(SAFETY)

    config.data_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(config.data_dir), prefix=".restore-", suffix=".tmp")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        shutil.copyfile(candidate, tmp)
        for suffix in ("-wal", "-shm"):
            Path(str(config.db_path) + suffix).unlink(missing_ok=True)
        os.replace(tmp, config.db_path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    logger.info("Restored %s over %s.", candidate.name, config.db_path)
    return config.db_path


# -----------------------------------------------------------------------------
# Command line
# -----------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    command = argv[1] if len(argv) > 1 else ""
    if command == "list":
        for path in list_backups():
            print(f"{path.name}  {path.stat().st_size // 1024} KB")
        return 0
    if command == "create":
        path = create_backup(SCHEDULED)
        print(path if path else "No database to back up yet.")
        return 0
    if command == "restore" and len(argv) == 3:
        restore_backup(argv[2])
        return 0
    print(__doc__.split("Command line")[1].strip())
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv))