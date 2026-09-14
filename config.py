# =============================================================================
# AI POWERED FINANCIAL DASHBOARD
# Static configuration and runtime settings.
# =============================================================================

"""Configuration for the AI Powered Financial Dashboard.

Everything that may legitimately change between deployments is read from
environment variables, with sane defaults for a local run. Nothing in this
module should contain secrets: credentials are read from the environment at
startup and are never committed to the repository.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent


def _env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean environment variable accepting common truthy spellings."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    """Read an integer environment variable, falling back on malformed input."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid integer for %s=%r; using default %d.", name, raw, default)
        return default


def _parse_users(raw: str | None) -> dict[str, str]:
    """Parse DASHBOARD_USERS in the form 'user1:pass1,user2:pass2'."""
    if not raw or not raw.strip():
        return {}
    users: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        username, password = pair.split(":", 1)
        username, password = username.strip(), password.strip()
        if username and password:
            users[username] = password
    return users


class Config:
    """Runtime configuration, resolved once at import time."""

    # ----- Ollama -----
    # Inside Docker, point this at the Ollama service or at the host gateway.
    ollama_url: str = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
    ollama_timeout: int = _env_int("OLLAMA_TIMEOUT", 120)
    ollama_retries: int = _env_int("OLLAMA_RETRIES", 1)

    # ----- Locale -----
    timezone: str = os.getenv("TZ", "UTC")
    # Free-form label used in the AI prompt so the model names amounts correctly.
    currency: str = os.getenv("CURRENCY", "BRL")

    # ----- Server -----
    host: str = os.getenv("DASH_HOST", "0.0.0.0")
    port: int = _env_int("DASH_PORT", 8050)
    debug: bool = _env_bool("DASH_DEBUG", False)
    # Opens a local splash page in the browser; meaningless inside a container.
    open_browser: bool = _env_bool("OPEN_BROWSER", False)

    # ----- Data file paths (relative to the project root / WORKDIR=/app) -----
    data_dir: Path = BASE_DIR / os.getenv("DATA_DIR", "data")
    csv_db: Path = data_dir / "data.csv"
    categories_db: Path = data_dir / "categories.csv"
    payment_methods_db: Path = data_dir / "payment_methods.csv"

    # ----- Authentication -----
    # Basic auth is only meaningful behind TLS or a trusted network (e.g. a VPN).
    request_password: bool = _env_bool("REQUEST_PASSWORD", False)
    valid_users: dict[str, str] = _parse_users(os.getenv("DASHBOARD_USERS"))

    # ----- Theme -------------------------------------------------------------
    # A dark, near-black surface palette. Names describe the role rather than
    # the hue so a future re-theme touches this block only.
    bg: str = "#0B0F14"             # page background
    surface: str = "#171717"        # cards, chart panels, modal bodies
    surface_raised: str = "#1F1F1F" # table headers, toolbar buttons
    border: str = "#444444"         # panel edges, chart gridlines
    text: str = "#E6E6E6"           # primary text on dark surfaces
    text_muted: str = "#8A8A8A"     # secondary text, placeholders
    accent: str = "#37C3F7"         # headings and key chart series
    # A deeper blue for filled buttons. The accent above is too light to carry
    # white text: it measures about 2:1, well under the 4.5:1 needed for body
    # text, so the label washes into the fill. This shade reaches 5.1:1.
    action: str = "#1773B0"

    # Panels (charts, cards, the table) share one outline so they read as the
    # same kind of surface. Defined once here; assets/theme.css repeats the
    # values for the elements Dash renders itself.
    panel_radius: str = "8px"

    # ----- Chart series ------------------------------------------------------
    # Muted pastels: saturated fills vibrate against a near-black background,
    # while these stay legible without competing with the accent.
    series_red: str = "#E8A49C"
    series_green: str = "#A8D5A0"
    series_yellow: str = "#E8C88A"

    # ----- Typography -----
    fontsize_1: str = "15px"     # Modal body, buttons, table
    fontsize_2: str = "20px"     # Modal titles
    fontsize_3: str = "18px"     # Filter labels
    chart_fontsize_1: int = 12   # Charts


# Schemas for the CSV files. Used to bootstrap empty files on first run and to
# validate that an existing file has the columns the app depends on.
TRANSACTION_COLUMNS: list[str] = [
    "Transaction Date",
    "Payment Date",
    "Label",
    "Category",
    "Amount",
    "Installment",
    "Payment Method",
    "Hash",
    "Record Timestamp",
    "Ignore Entry",
]

CATEGORY_COLUMNS: list[str] = ["Name"]

PAYMENT_METHOD_COLUMNS: list[str] = ["Name", "Close Date", "Payment Date", "Type"]

CSV_SEPARATOR: str = ";"
CSV_ENCODING: str = "utf-8-sig"

# Canonical on-disk date formats. The UI renders dates differently; these are
# the formats actually written to and read from the CSV files.
DATE_FORMAT: str = "%Y-%m-%d"
TIMESTAMP_FORMAT: str = "%Y-%m-%d %H:%M:%S"
