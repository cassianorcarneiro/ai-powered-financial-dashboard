# =============================================================================
# AI POWERED FINANCIAL DASHBOARD
# Privacy lock: password hashing and persisted on/off state.
# =============================================================================

"""A lightweight in-app lock, distinct from the HTTP Basic Auth in app.py.

The two solve different problems. Basic Auth (REQUEST_PASSWORD/DASHBOARD_USERS)
is a deployment-time secret meant to keep the dashboard off the network for
people without it; it is checked before Dash serves anything. This module is a
same-device privacy speed bump: something to type past before someone who
already has your phone or laptop unlocked can casually open the dashboard and
see your finances. It is not designed to resist someone willing to inspect
the server or its files.

Consistent with that goal, the password is hashed (PBKDF2-HMAC-SHA256, salted)
rather than stored in the clear, but there is no rate limiting, no lockout, and
the state file lives in the bind-mounted data/ directory alongside the CSVs.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from pathlib import Path

from config import Config as config

logger = logging.getLogger(__name__)

LOCK_FILE: Path = config.data_dir / "lock.json"

_PBKDF2_ITERATIONS = 260_000
_SALT_BYTES = 16


def _hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    ).hex()


def _load() -> dict:
    if not LOCK_FILE.exists():
        return {"enabled": False, "salt": None, "hash": None}
    try:
        return json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Could not read %s: %s. Treating the lock as disabled.", LOCK_FILE, exc)
        return {"enabled": False, "salt": None, "hash": None}


def _save(state: dict) -> None:
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.write_text(json.dumps(state), encoding="utf-8")


def is_enabled() -> bool:
    """Whether a fresh page load should show the password gate."""
    return bool(_load().get("enabled")) and has_password()


def has_password() -> bool:
    """Whether a password has ever been set, regardless of enabled state."""
    state = _load()
    return bool(state.get("salt")) and bool(state.get("hash"))


def check_password(password: str) -> bool:
    """Verify a password against the stored hash. False if none is set."""
    state = _load()
    salt_hex, expected = state.get("salt"), state.get("hash")
    if not salt_hex or not expected:
        return False
    candidate = _hash(password, bytes.fromhex(salt_hex))
    return hmac.compare_digest(candidate, expected)


def set_password(new_password: str) -> None:
    """Set or replace the password. Does not change the enabled state."""
    salt = os.urandom(_SALT_BYTES)
    state = _load()
    state["salt"] = salt.hex()
    state["hash"] = _hash(new_password, salt)
    _save(state)


def set_enabled(enabled: bool) -> None:
    """Turn the lock on or off for future page loads."""
    state = _load()
    state["enabled"] = bool(enabled)
    _save(state)
