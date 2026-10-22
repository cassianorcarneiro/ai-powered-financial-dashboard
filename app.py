# =============================================================================
# AI POWERED FINANCIAL DASHBOARD
# Application entry point: Dash app, callbacks and record generation.
# =============================================================================

"""Personal finance dashboard built with Dash, Plotly and a local Ollama model.

Transactions live in a CSV file on the host. The app renders aggregated charts,
lets the user manage payment methods and insert installment-aware records, and
generates a written commentary over the trailing twelve months.
"""

from __future__ import annotations

import logging
import os
import secrets
import uuid
from datetime import datetime
from typing import Any

import dash
import dash_bootstrap_components as dbc
import pandas as pd
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, callback_context, html, no_update
from dateutil.relativedelta import relativedelta
from flask import has_request_context, session
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import backup
import charts
import layout as ui
import security
from config import DATE_FORMAT, TIMESTAMP_FORMAT, Config as config
from insights import get_insight
from metrics import compute_window_metrics, parse_installment
from storage import (
    append_transactions,
    bootstrap_data_files,
    delete_transactions,
    get_categories,
    get_payment_method,
    get_payment_methods,
    load_categories,
    load_payment_methods,
    load_transactions,
    replace_transactions,
    save_categories,
    save_payment_methods,
)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("financial_dashboard")

# Internal keys carried in the table records but not rendered as columns. The
# hash identifies the purchase and the ISO date matches the stored value, so
# deletions never depend on the localized string shown to the user.
_ISO_DATE_KEY = "_payment_date_iso"
_HASH_KEY = "_hash"


# -----------------------------------------------------------------------------
# Record generation
# -----------------------------------------------------------------------------

def _nearest_year_for_month(reference: datetime, month: int) -> int:
    """Pick the calendar year that puts `month` closest to `reference`.

    A bare month number is ambiguous at a year boundary: if the automatic
    calculation lands on December 2026 and the override says "January", the
    intended January is 2027 (one month later), not 2026 (eleven months
    earlier) -- and the reverse holds for a reference of January 2027 with
    an override of December. Checking `reference`'s own year plus the one
    before and after, and keeping whichever puts `month` fewest months away,
    covers both directions with one rule; the reference and its override are
    always close enough in practice (a statement-boundary slip is never more
    than a month or two) that a one-year window is more than enough.
    """
    reference_index = reference.year * 12 + (reference.month - 1)
    candidate_years = (reference.year - 1, reference.year, reference.year + 1)
    return min(
        candidate_years,
        key=lambda year: abs((year * 12 + (month - 1)) - reference_index),
    )


def compute_first_payment_date(
    base_date: datetime,
    method: dict[str, Any],
    method_name: str,
    override_month: int | None = None,
) -> datetime:
    """Resolve when the first installment of a purchase is actually paid.

    For credit methods the answer depends on whether the purchase landed before
    or after the statement closed, and on whether the payment day falls before
    or after the close day within the month. Debit methods are paid immediately.

    `override_month` lets the result be pinned by hand, for the statement-
    boundary edge case where a purchase made right at the card's closing date
    lands in a different cycle than this calculation assumes. It is a bare
    month number (1-12), with no year attached -- the automatic calculation
    below still runs first to establish `target`, and the override only
    replaces its month, with the year resolved by _nearest_year_for_month
    against that automatic result. The day of month is untouched either way,
    picked the normal way (the card's payment day for Credit, the purchase's
    own day for Debit), so overriding never requires the user to work out
    which exact day the card bills on, only which month it fell in.
    """
    method_type = method["type"]

    if method_type == "Debit":
        target = base_date
        day = base_date.day
    elif method_type == "Credit":
        close_day = method["statement_close_day"]
        payment_day = method["payment_day"]
        if close_day is None or payment_day is None:
            raise ValueError(
                f"Credit method {method_name!r} is missing its close day or payment day."
            )

        closed_already = base_date.day > close_day
        # When the payment day precedes the close day it belongs to the
        # following month's cycle, which shifts every case one month further.
        offset = 0 if payment_day > close_day else 1
        offset += 1 if closed_already else 0
        target = base_date + relativedelta(months=offset)
        day = payment_day
    else:
        raise ValueError(f"Unknown payment method type: {method_type!r}")

    if override_month is not None:
        year = _nearest_year_for_month(target, override_month)
        # day=1 first: replacing only `month` on a day-31 target against a
        # shorter override month (e.g. target day 31, override month
        # February) would otherwise raise "day is out of range for month".
        # _safe_replace_day below sets the real day once the month is safe.
        target = target.replace(year=year, month=override_month, day=1)

    return _safe_replace_day(target, day)


def _safe_replace_day(moment: datetime, day: int) -> datetime:
    """Set the day of month, clamping to the last valid day for short months."""
    last_day = (moment.replace(day=1) + relativedelta(months=1, days=-1)).day
    return moment.replace(day=min(day, last_day))


def generate_installments(
    label: str,
    category: str,
    date_str: str,
    total_amount: float,
    installments: int,
    payment_method_name: str,
    ignore: bool,
    transaction_hash: str | None = None,
    payment_start_month: int | None = None,
) -> list[dict[str, Any]]:
    """Split a purchase into one record per installment.

    The sign of `total_amount` is preserved, so expenses stay negative. Rounding
    residue from the division is added to the final installment, which keeps the
    sum of the parts exactly equal to the original amount.

    `transaction_hash` defaults to a fresh id for a new purchase. Editing an
    existing purchase passes its current Hash back in instead, so the
    regenerated installments replace the old ones under the same identity
    rather than appearing as an unrelated purchase.

    `payment_start_month` is the optional manual override for the first
    installment's payment month (see compute_first_payment_date): a bare
    month number, 1-12, with the year resolved automatically. None means no
    override, the ordinary case.
    """
    if installments < 1:
        raise ValueError("The number of installments must be at least 1.")

    method = get_payment_method(payment_method_name)
    base_date = datetime.strptime(date_str, DATE_FORMAT)
    first_payment = compute_first_payment_date(
        base_date, method, payment_method_name, override_month=payment_start_month
    )

    per_installment = round(total_amount / installments, 2)
    residue = round(total_amount - per_installment * installments, 2)

    transaction_hash = transaction_hash or str(uuid.uuid4())
    recorded_at = datetime.now().strftime(TIMESTAMP_FORMAT)

    # relativedelta already clamps to the last valid day of a short month, so
    # a purchase billed on the 31st falls back to the 28th/30th as expected.
    records: list[dict[str, Any]] = []
    for index in range(1, installments + 1):
        amount = per_installment + (residue if index == installments else 0.0)
        payment_date = first_payment + relativedelta(months=index - 1)

        records.append(
            {
                "Transaction Date": base_date.strftime(DATE_FORMAT),
                "Payment Date": payment_date.strftime(DATE_FORMAT),
                "Label": label,
                "Category": category,
                "Amount": round(amount, 2),
                "Installment": f"{index}/{installments}",
                "Payment Method": payment_method_name,
                "Hash": transaction_hash,
                "Record Timestamp": recorded_at,
                "Ignore Entry": 1 if ignore else 0,
            }
        )
    return records


def load_purchase(hash_value: str) -> dict[str, Any] | None:
    """Reconstruct a purchase's original form-field values from storage.

    The table shows one row per installment: a per-installment Amount and an
    "index/total" Installment marker, never the total the user originally
    typed. Pre-filling the edit modal needs that original total and the
    original installment count back, not what any single surviving row
    displays.

    This reads every row still on disk for `hash_value`, not just the ones
    currently visible in the table, because the table's date filter can hide
    some of a purchase's installments without hiding the purchase itself.
    One surviving row is enough to reconstruct the total: multiplying its own
    Amount by the installment count embedded in its own marker reproduces the
    original total regardless of which installments happen to still exist.
    This is the same reconstruction `charts.purchase_totals_by_month` uses,
    applied to a single purchase instead of a whole month.

    Returns None if no row with this Hash exists any more (the purchase was
    deleted by another callback between the table render and this click).
    """
    df = load_transactions()
    rows = df[df["Hash"] == hash_value]
    if rows.empty:
        return None

    first = rows.iloc[0]
    parsed = parse_installment(first["Installment"])
    installment_index, installment_count = parsed if parsed else (1, 1)

    transaction_date = str(first["Transaction Date"])
    payment_method_name = str(first["Payment Method"])

    # The actual first-installment payment date, reconstructed the same way
    # the total is: any surviving row's own Payment Date, shifted back by its
    # own position in the schedule (generate_installments spaces installments
    # exactly one month apart), lands on installment 1's date regardless of
    # whether installment 1 itself is one of the rows still on disk.
    surviving_payment_date = datetime.strptime(str(first["Payment Date"]), DATE_FORMAT)
    actual_first_payment = surviving_payment_date - relativedelta(months=installment_index - 1)

    # The override field is pre-filled only when this purchase's actual
    # schedule disagrees with what today's automatic calculation would
    # produce from its own date and payment method -- i.e. only when
    # something (a prior manual override, or an edge case the formula
    # doesn't model) already made this purchase a special case. An ordinary
    # purchase edit -- fixing a label, say -- then leaves the field empty and
    # the schedule free to recompute normally. A payment method that no
    # longer exists can't be checked against, so the override is pre-filled
    # unconditionally then, to avoid silently shifting a schedule that can't
    # be verified.
    try:
        method = get_payment_method(payment_method_name)
        auto_first_payment = compute_first_payment_date(
            datetime.strptime(transaction_date, DATE_FORMAT), method, payment_method_name
        )
        diverges = (auto_first_payment.year, auto_first_payment.month) != (
            actual_first_payment.year, actual_first_payment.month,
        )
    except (KeyError, ValueError):
        diverges = True
    # The Select's options carry string values ("1".."12", or "" for the
    # blank "Automatic" choice), and this is fed straight into that field's
    # `value` by open_edit_modal -- an int here would simply match none of
    # the options and leave the control showing blank.
    payment_start_month = str(actual_first_payment.month) if diverges else ""

    return {
        "label": str(first["Label"]),
        "category": str(first["Category"]),
        "transaction_date": transaction_date,
        "amount": round(float(first["Amount"]) * installment_count, 2),
        "installments": installment_count,
        "payment_method": payment_method_name,
        "ignore": bool(int(first["Ignore Entry"])),
        "payment_start_month": payment_start_month,
    }


# -----------------------------------------------------------------------------
# Presentation helpers
# -----------------------------------------------------------------------------

def current_timestamp() -> str:
    """Current time in the configured zone, degrading to UTC if it is unknown."""
    try:
        zone = ZoneInfo(config.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("Unknown timezone %r; falling back to UTC.", config.timezone)
        zone = ZoneInfo("UTC")
    return datetime.now(zone).strftime(TIMESTAMP_FORMAT)


# Sort keys are taken from the underlying values, never from the formatted text.
# A date rendered as dd/mm/yyyy and an amount rendered with two decimals both
# sort wrongly as strings: "01/12/2025" precedes "09/01/2026" alphabetically, and
# "-50.00" precedes "1000.00". The table therefore sorts server-side
# (sort_action="custom") over the real datetime and float values.
def _installment_index(value: object) -> int:
    """Numeric sort key for an installment marker such as '2/6'."""
    parsed = parse_installment(value)
    return parsed[0] if parsed else 0


def _sort_view(view: pd.DataFrame, sort_by: list[dict[str, Any]] | None) -> pd.DataFrame:
    """Order the table by the requested columns, using pre-format values."""
    if not sort_by:
        return view.sort_values("Record Timestamp", ascending=False, na_position="last")

    keys, ascending = [], []
    for rule in sort_by:
        column = rule.get("column_id")
        if column not in view.columns:
            continue
        keys.append(column)
        ascending.append(rule.get("direction", "asc") == "asc")

    if not keys:
        return view.sort_values("Record Timestamp", ascending=False, na_position="last")

    scratch = view.copy()
    sort_columns = []
    for column in keys:
        if column == "Installment":
            helper = "__sort_installment"
            scratch[helper] = scratch["Installment"].map(_installment_index)
            sort_columns.append(helper)
        else:
            # Datetime and Amount columns are still typed at this point; string
            # columns sort naturally.
            sort_columns.append(column)

    scratch = scratch.sort_values(sort_columns, ascending=ascending, na_position="last")
    return scratch.drop(columns=[c for c in scratch.columns if c.startswith("__sort_")])


def build_table_records(
    df: pd.DataFrame,
    sort_by: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Format transactions for display, newest first unless sorted otherwise."""
    if df.empty:
        return []

    view = df.copy()
    for column, fmt in (
        ("Record Timestamp", TIMESTAMP_FORMAT),
        ("Transaction Date", DATE_FORMAT),
        ("Payment Date", DATE_FORMAT),
    ):
        view[column] = pd.to_datetime(view[column], errors="coerce", format=fmt)

    view = _sort_view(view, sort_by)
    # Preserved so deletions can match on the canonical stored values.
    view[_ISO_DATE_KEY] = view["Payment Date"].dt.strftime(DATE_FORMAT)
    view[_HASH_KEY] = view["Hash"]

    view["Record Timestamp"] = view["Record Timestamp"].dt.strftime("%d/%m/%Y %H:%M:%S")
    view["Transaction Date"] = view["Transaction Date"].dt.strftime("%d/%m/%Y")
    view["Payment Date"] = view["Payment Date"].dt.strftime("%d/%m/%Y")
    # Two decimals, no thousands separator: the grouping comma reads as a decimal
    # mark in most of the world, so in a table of bare numbers it is ambiguous.
    view["Amount"] = view["Amount"].map(lambda v: f"{v:.2f}")
    # Constant glyph, not derived from the row: clicking it is what the
    # "open-edit-modal" callback below listens for, via active_cell's
    # column_id. It carries no data of its own.
    view[ui.EDIT_COLUMN] = ui.EDIT_ICON

    return view[ui.TABLE_COLUMNS + [_ISO_DATE_KEY, _HASH_KEY]].to_dict("records")


_DISPLAY_DATE_FORMAT = "%d/%m/%Y"


def parse_display_date(raw: object) -> str:
    """Read the Transaction Date field (DD/MM/YYYY) and return it as stored (ISO).

    Strict on purpose: the field is free text now, so an impossible date such
    as 31/02/2026 or a half-typed one such as 12/03/20 has to be refused here
    rather than reach the CSV/database. The year is bounded to catch a
    slipped digit (0026, 2062) that would otherwise be a valid but absurd date.
    """
    text = str(raw or "").strip()
    try:
        parsed = datetime.strptime(text, _DISPLAY_DATE_FORMAT)
    except ValueError:
        raise ValueError("Transaction Date must be a valid date as DD/MM/YYYY.") from None
    if not 1900 <= parsed.year <= 2100:
        raise ValueError("Transaction Date must be between the years 1900 and 2100.")
    return parsed.strftime(DATE_FORMAT)


def format_display_date(iso_date: str) -> str:
    """The stored ISO date as shown in the Transaction Date field."""
    return datetime.strptime(iso_date, DATE_FORMAT).strftime(_DISPLAY_DATE_FORMAT)


def parse_amount(raw: object) -> float:
    """Read the amount field, which the client-side mask renders as text.

    The mask produces a plain decimal such as "-123.45", but the field stays
    usable if the script never loads, so this also accepts what someone would
    type by hand: stray spaces, a leading "+", or a comma used as the decimal
    mark. A thousands separator is not accepted, because the same character
    would be ambiguous against a comma decimal mark.
    """
    if isinstance(raw, (int, float)):
        return float(raw)

    text = str(raw or "").strip().replace(" ", "").lstrip("+")
    if not text:
        raise ValueError("empty amount")
    if text.count(",") == 1 and "." not in text:
        text = text.replace(",", ".")
    return float(text)


def alert(message: str, color: str = "danger") -> dbc.Alert:
    """Short-lived inline feedback message."""
    return dbc.Alert(message, color=color, className="py-2 mb-0", dismissable=True)


def selected_records(
    rows: list[dict[str, Any]] | None,
    selected: list[int] | None,
) -> list[dict[str, Any]]:
    """Resolve selected indices against the rows currently visible in the table."""
    if not rows or not selected:
        return []
    return [rows[i] for i in selected if 0 <= i < len(rows)]


# -----------------------------------------------------------------------------
# App
# -----------------------------------------------------------------------------

bootstrap_data_files()
backup.start_scheduler()

app = Dash(
    __name__,
    external_stylesheets=[dbc.themes.BOOTSTRAP, dbc.icons.FONT_AWESOME],
    title="Financial Control",
    update_title=None,
    # Required because of the privacy lock: unlock_dashboard's callback
    # references lock-redirect/lock-gate-error, which only exist in the gate
    # layout (ui.build_lock_gate), and manage_lock_password/toggle_lock
    # reference toolbar ids that only exist in the dashboard layout
    # (ui.build_layout). The two never render together — serve_layout()
    # picks exactly one per request — but every @app.callback runs
    # unconditionally at import time, so Dash's default validation checks all
    # of them against whichever layout happens to be current and warns about
    # the other set. This is the standard, Dash-documented setting for an app
    # with more than one top-level layout; it does mean a genuine typo'd
    # component id elsewhere would no longer be caught by that validation, so
    # id wiring is instead checked directly in this project's test suite.
    suppress_callback_exceptions=True,
)
def serve_layout() -> html.Div:
    """Chooses the lock gate or the real dashboard for this page load.

    Runs on the server for every full page load (Dash calls a callable
    `app.layout` fresh each time), with access to the Flask session tied to
    the request. That is what makes the gate real rather than cosmetic: when
    it applies, the dashboard's own components — the ones the data flows
    through — are simply never constructed for this response.

    Dash also calls this function once at startup, outside any HTTP request,
    to validate the layout against the registered callbacks. `flask.session`
    does not exist yet at that point, so reading it raises RuntimeError. The
    `has_request_context()` check below covers exactly that call: with no
    request in flight there is no session to check, and no data is being sent
    to anyone, so it is safe to render the real layout for validation purposes.
    """
    locked = security.is_enabled() and has_request_context() and not session.get("unlocked")
    return ui.build_lock_gate() if locked else ui.build_layout()


app.layout = serve_layout
server = app.server  # WSGI entry point used by gunicorn

# Required for Flask's signed session cookie, which is how "unlocked" survives
# from one page load to the next. Generated fresh per process rather than
# persisted to disk: the password itself survives a restart (it's on disk via
# security.py), but an already-unlocked session does not, which is the
# reasonable default for a privacy speed bump — nothing to leak if the key
# were ever read off the container.
server.secret_key = secrets.token_hex(32)

# Dash's default template already inserts assets/favicon.ico via {%favicon%},
# so the browser tab icon needs no extra markup. These three tags cover what
# that placeholder does not: the manifest is what lets Chrome on Android draw
# a real icon (rather than a cropped screenshot) when the page is added to the
# home screen; apple-touch-icon is Safari/iOS's separate equivalent; and
# theme-color tints the browser chrome so the shortcut feels like an app
# rather than a bookmark.
app.index_string = """<!DOCTYPE html>
<html>
    <head>
        {%metas%}
        <title>{%title%}</title>
        {%favicon%}
        {%css%}
        <link rel="manifest" href="/assets/manifest.json">
        <link rel="apple-touch-icon" href="/assets/apple-touch-icon.png">
        <meta name="theme-color" content="#0B0F14">
    </head>
    <body>
        {%app_entry%}
        <footer>
            {%config%}
            {%scripts%}
            {%renderer%}
        </footer>
    </body>
</html>"""

if config.request_password:
    if not config.valid_users:
        raise SystemExit(
            "REQUEST_PASSWORD is enabled but DASHBOARD_USERS is empty. "
            "Set DASHBOARD_USERS='user:password' or disable REQUEST_PASSWORD."
        )
    import dash_auth

    dash_auth.BasicAuth(app, config.valid_users)
    logger.info("Basic authentication enabled for %d user(s).", len(config.valid_users))


@server.route("/healthz")
def healthz():
    """Liveness probe that does not render the full page."""
    return {"status": "ok"}, 200


# -----------------------------------------------------------------------------
# Callbacks: privacy lock
# -----------------------------------------------------------------------------

@app.callback(
    Output("lock-redirect", "href"),
    Output("lock-gate-error", "children"),
    Input("lock-submit-btn", "n_clicks"),
    Input("lock-password-input", "n_submit"),  # fires on Enter in the field
    State("lock-password-input", "value"),
    prevent_initial_call=True,
)
def unlock_dashboard(_n_clicks, _n_submit, password):
    """Verify the password and, on success, force a real page reload.

    The reload matters: `session["unlocked"]` only takes effect for
    serve_layout() on the *next* full page load, not the current one, so a
    client-side-only update would leave the gate showing despite success.
    """
    if security.check_password(password or ""):
        session["unlocked"] = True
        return "/", None
    return no_update, alert("Incorrect password.")


@app.callback(
    Output("lock-toggle-col", "children"),
    Output("lock-toggle-feedback", "children"),
    Input("toggle-lock-btn", "n_clicks"),
    prevent_initial_call=True,
)
def toggle_lock(_n_clicks):
    """Flip the lock for future page loads; the current tab is unaffected.

    Turning the lock on with no password set would lock the dashboard behind a
    password nobody has typed yet, so that combination is refused with a
    message pointing at the password button instead.
    """
    currently_on = security.is_enabled()

    if not currently_on and not security.has_password():
        icon = ui._icon_button(
            "fa-solid fa-lock-open", "toggle-lock-btn", "Lock the dashboard on future visits"
        )
        return icon, alert(
            "Set a password first, using the key button.", color="warning"
        )

    security.set_enabled(not currently_on)
    now_on = not currently_on
    icon = ui._icon_button(
        "fa-solid fa-lock" if now_on else "fa-solid fa-lock-open",
        "toggle-lock-btn",
        "Lock the dashboard on future visits",
    )
    message = (
        "Lock enabled. The password will be requested the next time this page loads."
        if now_on
        else "Lock disabled."
    )
    return icon, alert(message, color="success")


@app.callback(
    Output("modal-lock-password", "is_open"),
    Output("lock-password-feedback", "children"),
    Output("lock-current-password", "value"),
    Output("lock-new-password", "value"),
    Output("lock-confirm-password", "value"),
    Input("open-lock-password-modal", "n_clicks"),
    Input("btn-close-lock-password", "n_clicks"),
    Input("btn-save-lock-password", "n_clicks"),
    State("modal-lock-password", "is_open"),
    State("lock-current-password", "value"),
    State("lock-new-password", "value"),
    State("lock-confirm-password", "value"),
    prevent_initial_call=True,
)
def manage_lock_password(_open, _close, _save, is_open, current_pw, new_pw, confirm_pw):
    """Open, close, or persist a change to the lock password.

    Requires the existing password to change it, except the very first time
    one is set, when there is nothing yet to check against.
    """
    if callback_context.triggered_id == "btn-save-lock-password":
        if security.has_password() and not security.check_password(current_pw or ""):
            return True, alert("Current password is incorrect."), no_update, no_update, no_update
        if not new_pw:
            return True, alert("Enter a new password."), no_update, no_update, no_update
        if new_pw != confirm_pw:
            return True, alert("New passwords do not match."), no_update, no_update, no_update

        security.set_password(new_pw)
        logger.info("Dashboard lock password changed.")
        return False, None, "", "", ""

    return (not is_open), None, "", "", ""



# -----------------------------------------------------------------------------

@app.callback(
    Output("filters-collapse", "is_open"),
    Input("toggle-filters", "n_clicks"),
    State("filters-collapse", "is_open"),
    prevent_initial_call=True,
)
def toggle_filters(_n_clicks, is_open):
    """Show or hide the date-range filter."""
    return not is_open


@app.callback(
    Output("date-start", "date"),
    Output("date-end", "date"),
    Input("reset-btn", "n_clicks"),
    prevent_initial_call=True,
)
def reset_filters(_n_clicks):
    """Restore the default date range."""
    return ui.default_date_range()


# -----------------------------------------------------------------------------
# Callbacks: payment methods
# -----------------------------------------------------------------------------

def _collect_payment_methods(names, types, close_days, pay_days) -> list[dict[str, Any]]:
    """Rebuild records from the pattern-matched inputs, dropping blank rows."""
    records = []
    for name, method_type, close_day, pay_day in zip(
        names or [], types or [], close_days or [], pay_days or []
    ):
        name = (name or "").strip()
        if not name and not method_type:
            continue
        records.append(
            {
                "Name": name,
                "Type": method_type or "",
                "Close Date": close_day if close_day not in (None, "") else "",
                "Payment Date": pay_day if pay_day not in (None, "") else "",
            }
        )
    return records


def _validate_payment_methods(rows: list[dict[str, Any]]) -> None:
    """Reject payment methods that would break installment date computation."""
    seen: set[str] = set()
    for row in rows:
        name = str(row.get("Name", "")).strip()
        method_type = str(row.get("Type", "")).strip()
        if not name:
            raise ValueError("Every payment method needs a name.")
        if name in seen:
            raise ValueError(f"Duplicate payment method name: {name}.")
        seen.add(name)
        if method_type not in {"Credit", "Debit"}:
            raise ValueError(f"{name}: choose a type, either Credit or Debit.")
        if method_type == "Credit":
            for field in ("Close Date", "Payment Date"):
                raw = row.get(field)
                if raw in (None, "", "nan"):
                    raise ValueError(f"{name}: credit methods require a {field.lower()}.")
                try:
                    day = int(float(raw))
                except (TypeError, ValueError):
                    raise ValueError(f"{name}: {field.lower()} must be a whole number.") from None
                if not 1 <= day <= 31:
                    raise ValueError(f"{name}: {field.lower()} must be between 1 and 31.")


@app.callback(
    Output("modal-payment-methods", "is_open"),
    Output("pm-rows-container", "children"),
    Output("input-payment-method", "options"),
    Output("payment-methods-feedback", "children"),
    Input("open-payment-methods-modal", "n_clicks"),
    Input("btn-close-payment-methods", "n_clicks"),
    Input("btn-save-payment-methods", "n_clicks"),
    Input("btn-add-payment-method", "n_clicks"),
    Input({"type": "pm-delete", "index": dash.ALL}, "n_clicks"),
    State("modal-payment-methods", "is_open"),
    State({"type": "pm-name", "index": dash.ALL}, "value"),
    State({"type": "pm-type", "index": dash.ALL}, "value"),
    State({"type": "pm-close", "index": dash.ALL}, "value"),
    State({"type": "pm-pay", "index": dash.ALL}, "value"),
    prevent_initial_call=True,
)
def manage_payment_methods(
    _open, _close, _save, _add, _deletes, is_open, names, types, close_days, pay_days
):
    """Open, close, add, delete, or persist payment methods.

    Every branch re-renders the whole row list from the values currently in the
    inputs, so edits typed before pressing Add or Delete are never discarded.
    """
    trigger = callback_context.triggered_id
    current = _collect_payment_methods(names, types, close_days, pay_days)
    options = lambda: [{"label": m, "value": m} for m in get_payment_methods()]

    if trigger == "btn-add-payment-method":
        current.append({"Name": "", "Type": "", "Close Date": "", "Payment Date": ""})
        return True, ui.build_payment_method_rows(current), options(), None

    if isinstance(trigger, dict) and trigger.get("type") == "pm-delete":
        # Ignore the callback Dash fires when the delete buttons are first drawn.
        if not any(_deletes or []):
            return no_update, no_update, no_update, no_update
        index = trigger["index"]
        remaining = [r for i, r in enumerate(current) if i != index]
        return True, ui.build_payment_method_rows(remaining), options(), None

    if trigger == "btn-save-payment-methods":
        try:
            _validate_payment_methods(current)
        except ValueError as exc:
            return True, ui.build_payment_method_rows(current), options(), alert(str(exc))
        save_payment_methods(pd.DataFrame(current))
        logger.info("Saved %d payment method(s).", len(current))
        saved = load_payment_methods().to_dict("records")
        return False, ui.build_payment_method_rows(saved), options(), None

    # Open or close: always show what is actually on disk.
    stored = load_payment_methods().to_dict("records")
    return (not is_open), ui.build_payment_method_rows(stored), options(), None


# -----------------------------------------------------------------------------
# Callbacks: categories
# -----------------------------------------------------------------------------

def _collect_categories(names) -> list[dict[str, Any]]:
    """Rebuild category records from the pattern-matched inputs."""
    return [{"Name": (name or "").strip()} for name in (names or [])]


@app.callback(
    Output("modal-categories", "is_open"),
    Output("cat-rows-container", "children"),
    Output("input-category", "options"),
    Output("categories-feedback", "children"),
    Input("open-categories-modal", "n_clicks"),
    Input("btn-close-categories", "n_clicks"),
    Input("btn-save-categories", "n_clicks"),
    Input("btn-add-category", "n_clicks"),
    Input({"type": "cat-delete", "index": dash.ALL}, "n_clicks"),
    State("modal-categories", "is_open"),
    State({"type": "cat-name", "index": dash.ALL}, "value"),
    prevent_initial_call=True,
)
def manage_categories(_open, _close, _save, _add, _deletes, is_open, names):
    """Open, close, add, delete, or persist categories."""
    trigger = callback_context.triggered_id
    current = _collect_categories(names)
    options = lambda: [{"label": c, "value": c} for c in get_categories()]

    if trigger == "btn-add-category":
        current.append({"Name": ""})
        return True, ui.build_category_rows(current), options(), None

    if isinstance(trigger, dict) and trigger.get("type") == "cat-delete":
        if not any(_deletes or []):
            return no_update, no_update, no_update, no_update
        index = trigger["index"]
        remaining = [r for i, r in enumerate(current) if i != index]
        return True, ui.build_category_rows(remaining), options(), None

    if trigger == "btn-save-categories":
        named = [r for r in current if r["Name"]]
        duplicates = {r["Name"] for r in named if [x["Name"] for x in named].count(r["Name"]) > 1}
        if duplicates:
            return (
                True,
                ui.build_category_rows(current),
                options(),
                alert(f"Duplicate category name(s): {', '.join(sorted(duplicates))}."),
            )
        save_categories(pd.DataFrame(named))
        logger.info("Saved %d category(ies).", len(named))
        saved = load_categories().to_dict("records")
        return False, ui.build_category_rows(saved), options(), None

    stored = load_categories().to_dict("records")
    return (not is_open), ui.build_category_rows(stored), options(), None


# -----------------------------------------------------------------------------
# Callbacks: new record modal
# -----------------------------------------------------------------------------

def _record_dropdown_options() -> tuple[list[dict], list[dict]]:
    """Fresh (category, payment method) dropdown options for the record modal."""
    return (
        [{"label": c, "value": c} for c in get_categories()],
        [{"label": m, "value": m} for m in get_payment_methods()],
    )


@app.callback(
    Output("modal", "is_open"),
    Output("input-category", "options", allow_duplicate=True),
    Output("input-payment-method", "options", allow_duplicate=True),
    Output("modal-title", "children", allow_duplicate=True),
    Output("btn-save", "children", allow_duplicate=True),
    Output("editing-hash", "data", allow_duplicate=True),
    Output("input-label", "value", allow_duplicate=True),
    Output("input-category", "value", allow_duplicate=True),
    Output("input-date", "value", allow_duplicate=True),
    Output("input-amount", "value", allow_duplicate=True),
    Output("input-installments", "value", allow_duplicate=True),
    Output("input-payment-method", "value", allow_duplicate=True),
    Output("input-ignore", "value", allow_duplicate=True),
    Output("record-feedback", "children", allow_duplicate=True),
    Output("input-payment-start-month", "value", allow_duplicate=True),
    Input("open-modal", "n_clicks"),
    Input("btn-close", "n_clicks"),
    State("modal", "is_open"),
    prevent_initial_call=True,
)
def toggle_record_modal(_open, _close, is_open):
    """Toggle the record modal for a brand new record.

    Always resets to "add new" mode, even if the modal was last left in "edit"
    mode by a cancelled edit: this is the only other way the modal opens, so
    there's no case where an edit should still be in effect here. Also clears
    any leftover feedback message from a previous open, so closing and
    reopening the modal doesn't leave a stale "Updated ..." alert on screen.

    The payment-start-month override resets here too, even though save_record
    deliberately leaves it alone between consecutive inserts (see there for
    why): this is a fresh start (or a full stop, via Cancel), not "the next
    purchase from the same statement", so there's no reason for a value typed
    for one purchase -- or pre-filled by editing an unrelated one -- to carry
    into whatever gets added next.
    """
    category_options, method_options = _record_dropdown_options()
    return (
        (not is_open),
        category_options,
        method_options,
        "New Record",
        "Save",
        None,
        "",
        None,
        "",
        "0.00",
        1,
        None,
        False,
        None,
        "",
    )


@app.callback(
    Output("modal", "is_open", allow_duplicate=True),
    Output("input-category", "options", allow_duplicate=True),
    Output("input-payment-method", "options", allow_duplicate=True),
    Output("modal-title", "children", allow_duplicate=True),
    Output("btn-save", "children", allow_duplicate=True),
    Output("editing-hash", "data", allow_duplicate=True),
    Output("input-label", "value", allow_duplicate=True),
    Output("input-category", "value", allow_duplicate=True),
    Output("input-date", "value", allow_duplicate=True),
    Output("input-amount", "value", allow_duplicate=True),
    Output("input-installments", "value", allow_duplicate=True),
    Output("input-payment-method", "value", allow_duplicate=True),
    Output("input-ignore", "value", allow_duplicate=True),
    Output("record-feedback", "children", allow_duplicate=True),
    Output("table-data", "active_cell", allow_duplicate=True),
    Output("input-payment-start-month", "value", allow_duplicate=True),
    Input("table-data", "active_cell"),
    State("table-data", "derived_virtual_data"),
    prevent_initial_call=True,
)
def open_edit_modal(active_cell, rows):
    """Open the record modal pre-filled with an existing purchase.

    Triggers only on a click in the Edit column specifically (every other
    column click leaves active_cell set but is ignored here), identified by
    active_cell's own column_id rather than by a separate button component --
    DataTable cells aren't real Dash components, so there is nothing else to
    attach a click handler to.

    active_cell is reset back to None on every path out of here (other than
    the two early PreventUpdate returns, which leave everything untouched).
    Dash's Input only fires when a prop's value actually changes, so without
    this a second click on the very same icon -- the single most likely next
    thing to click -- would leave active_cell identical to what it already
    was and silently do nothing.
    """
    if not active_cell or active_cell.get("column_id") != ui.EDIT_COLUMN:
        raise dash.exceptions.PreventUpdate

    row_index = active_cell["row"]
    if not rows or row_index >= len(rows):
        raise dash.exceptions.PreventUpdate

    hash_value = rows[row_index][_HASH_KEY]
    purchase = load_purchase(hash_value)
    if purchase is None:
        return (
            no_update, no_update, no_update, no_update, no_update, no_update,
            no_update, no_update, no_update, no_update, no_update, no_update,
            alert("That record no longer exists; the table will refresh."),
            None,
            no_update,
            no_update,
        )

    category_options, method_options = _record_dropdown_options()
    return (
        True,
        category_options,
        method_options,
        "Edit Record",
        "Save changes",
        hash_value,
        purchase["label"],
        purchase["category"],
        format_display_date(purchase["transaction_date"]),
        f"{purchase['amount']:.2f}",
        purchase["installments"],
        purchase["payment_method"],
        purchase["ignore"],
        # Clears any leftover feedback from a previous save, same as
        # toggle_record_modal does for the "add new" path -- otherwise a
        # stale "Updated ..." alert from the last edit would still be
        # showing the next time this modal opens.
        None,
        None,
        # Pre-filled only when load_purchase found this purchase's actual
        # schedule diverging from what today's automatic calculation would
        # produce -- None otherwise, leaving the field empty and the
        # schedule free to recompute normally on save.
        purchase["payment_start_month"],
    )


# -----------------------------------------------------------------------------
# Callbacks: insert and delete
# -----------------------------------------------------------------------------

@app.callback(
    Output("update-trigger", "data"),
    Output("record-feedback", "children"),
    Output("input-label", "value"),
    Output("input-category", "value"),
    Output("input-date", "value"),
    Output("input-amount", "value"),
    Output("input-installments", "value"),
    Output("input-ignore", "value"),
    Output("editing-hash", "data", allow_duplicate=True),
    Output("modal-title", "children", allow_duplicate=True),
    Output("btn-save", "children", allow_duplicate=True),
    Output("modal", "is_open", allow_duplicate=True),
    Output("save-toast", "is_open", allow_duplicate=True),
    Output("save-toast", "children", allow_duplicate=True),
    Output("save-toast", "header", allow_duplicate=True),
    Input("btn-save", "n_clicks"),
    State("input-label", "value"),
    State("input-category", "value"),
    State("input-date", "value"),
    State("input-amount", "value"),
    State("input-installments", "value"),
    State("input-payment-method", "value"),
    State("input-ignore", "value"),
    State("input-payment-start-month", "value"),
    State("update-trigger", "data"),
    State("editing-hash", "data"),
    prevent_initial_call=True,
)
def save_record(
    _n_clicks, label, category, date_value, amount, installments,
    payment_method, ignore, payment_start_month, trigger_value, editing_hash,
):
    """Validate the form, then either append a new purchase or replace one.

    `editing_hash` set (from the Edit-column click) means this is a
    correction: every existing row for that Hash is deleted and the full
    installment schedule is regenerated under the same Hash from the edited
    fields. This intentionally regenerates installments that were previously
    deleted one at a time -- see the "Regenerar tudo" choice this was built
    against. `editing_hash` absent means a normal new-purchase insert,
    unchanged from before.

    The modal itself only closes on a successful edit. A successful insert
    leaves it open, since adding several new records in a row is the whole
    point of that flow; an edit targets one specific purchase, so there's
    nothing left to do in the modal once it's saved. Either way, a
    validation error keeps the modal open so the user can fix the field.

    A validation or save error is shown inline, in `record-feedback`, since
    the modal stays open for the user to correct it right there. A success
    message instead goes to the `save-toast` popup outside the modal: an
    insert leaves the modal open to show it alongside, but an edit closes
    the modal on success, and a message written into `record-feedback` would
    have closed along with it and never been seen.

    `input-payment-start-month` is deliberately not one of this callback's
    outputs, on any path -- it is the one field this callback never resets.
    Several purchases from the same card statement can need the same manual
    override in a row, and the insert flow's whole point is adding several
    purchases without reopening the modal, so forcing it to be retyped for
    each one would defeat that. toggle_record_modal and open_edit_modal
    still control it on their own triggers (a fresh "add new" open, a Cancel,
    or editing a specific purchase), so it never leaks from an edit or a
    finished session into an unrelated next purchase; see their docstrings.
    """
    missing = [
        name
        for name, value in (
            ("Label", label),
            ("Category", category),
            ("Transaction Date", date_value),
            ("Amount", amount),
            ("Payment Method", payment_method),
        )
        if value in (None, "", [])
    ]
    if missing:
        return (
            no_update,
            alert(f"Missing required field(s): {', '.join(missing)}."),
            *[no_update] * 13,
        )

    try:
        iso_date = parse_display_date(date_value)
    except ValueError as exc:
        return no_update, alert(str(exc)), *[no_update] * 13

    try:
        amount_value = parse_amount(amount)
    except ValueError:
        return no_update, alert("Amount must be a number."), *[no_update] * 13

    if amount_value == 0:
        return no_update, alert("Amount cannot be zero."), *[no_update] * 13

    try:
        installment_count = int(installments or 1)
    except (TypeError, ValueError):
        return no_update, alert("Installments must be a whole number."), *[no_update] * 13

    if installment_count < 1:
        return no_update, alert("Installments must be at least 1."), *[no_update] * 13

    # The Select's blank "Automatic" option carries value "" -- falsy, but
    # "" is not None, so compute_first_payment_date's `if override_month is
    # not None` check would wrongly treat it as a real override without this
    # conversion. A filled option carries the month as a string ("1".."12"),
    # which still needs casting to int before it reaches date arithmetic.
    try:
        payment_start_month = int(payment_start_month) if payment_start_month else None
    except (TypeError, ValueError):
        return no_update, alert("Payment Start Month is invalid."), *[no_update] * 13

    try:
        records = generate_installments(
            label=str(label).strip(),
            category=str(category),
            date_str=iso_date,
            total_amount=amount_value,
            installments=installment_count,
            payment_method_name=str(payment_method),
            ignore=bool(ignore),
            transaction_hash=editing_hash,
            payment_start_month=payment_start_month,
        )
        if editing_hash:
            removed = replace_transactions(
                lambda df: df["Hash"] == editing_hash, records
            )
            logger.info(
                "Updated purchase %r: replaced %d old record(s) with %d new "
                "record(s) for %r.", editing_hash, removed, len(records), label,
            )
            toast_header = "Record updated"
            toast_message = (
                f"Updated '{label}': {removed} old record(s) replaced with "
                f"{len(records)} new record(s)."
            )
        else:
            append_transactions(records)
            logger.info("Inserted %d record(s) for %r.", len(records), label)
            toast_header = "Record added"
            toast_message = f"Added {len(records)} record(s) for '{label}'."
    except (ValueError, KeyError) as exc:
        logger.error("Failed to save record: %s", exc)
        return no_update, alert(str(exc)), *[no_update] * 13

    # Amount resets to "0.00" rather than blank so the mask has a value to
    # build on from the first keystroke. The fields and the "add new" mode
    # reset regardless of which mode this save was, so a closed edit modal
    # reopens clean and an open insert modal is ready for the next record.
    # record-feedback is cleared (not reused for the success text) so it
    # doesn't show a second, redundant copy of what the toast already says.
    was_editing = bool(editing_hash)
    return (
        (trigger_value or 0) + 1, None, "", None, "", "0.00", 1, False,
        None, "New Record", "Save", (not was_editing),
        True, toast_message, toast_header,
    )


@app.callback(
    Output("update-trigger", "data", allow_duplicate=True),
    Output("delete-feedback", "children"),
    Input("btn-delete-hash-selected", "n_clicks"),
    Input("btn-delete-selected", "n_clicks"),
    State("table-data", "derived_virtual_data"),
    State("table-data", "derived_virtual_selected_rows"),
    State("update-trigger", "data"),
    prevent_initial_call=True,
)
def delete_records(_delete_purchase, _delete_installments, rows, selected, trigger_value):
    """Delete either whole purchases or individual installments.

    Selection is resolved against `derived_virtual_data`, which reflects the
    sorting and filtering the user applied. Using the raw `data` prop here would
    delete the wrong rows whenever the table is sorted.
    """
    trigger = callback_context.triggered_id
    chosen = selected_records(rows, selected)
    if not chosen:
        return no_update, alert("Select at least one row first.", color="warning")

    if trigger == "btn-delete-hash-selected":
        hashes = {row[_HASH_KEY] for row in chosen}
        removed = delete_transactions(lambda df: df["Hash"].isin(hashes))
        message = f"Deleted {removed} record(s) across {len(hashes)} purchase(s)."
    else:
        keys = {(row[_HASH_KEY], row[_ISO_DATE_KEY]) for row in chosen}
        removed = delete_transactions(
            lambda df: pd.Series(
                list(zip(df["Hash"], df["Payment Date"])), index=df.index
            ).isin(keys)
        )
        message = f"Deleted {removed} installment(s)."

    logger.info(message)
    return (trigger_value or 0) + 1, alert(message, color="success")


# -----------------------------------------------------------------------------
# Callbacks: charts and table
# -----------------------------------------------------------------------------

@app.callback(
    Output("table-data", "data"),
    Output("table-data", "selected_rows"),
    Input("update-trigger", "data"),
    Input("table-data", "sort_by"),
)
def refresh_table(_trigger, sort_by):
    """Rebuild the transactions table, honouring the requested sort order.

    Kept separate from the charts so re-sorting does not recompute seven
    figures, and so the table keeps showing every record regardless of the
    date filter applied to the charts.
    """
    return build_table_records(load_transactions(), sort_by), []


@app.callback(
    Output("fig-cumulative", "figure"),
    Output("fig-method-share", "figure"),
    Output("fig-category-share", "figure"),
    Output("fig-paid-monthly", "figure"),
    Output("fig-spent-monthly", "figure"),
    Output("fig-finishing", "figure"),
    Output("fig-starting", "figure"),
    Input("update-trigger", "data"),
    Input("date-start", "date"),
    Input("date-end", "date"),
)
def refresh_views(_trigger, start_date, end_date):
    """Rebuild every chart for the selected period.

    "Ignore Entry" is applied narrowly: it removes a record from the category
    breakdown and from the metrics the language model sees, but leaves it in
    every other chart. The flag marks a record whose category would distort the
    breakdown, not one that should vanish from the account's totals.
    """
    df = load_transactions()

    def _spent_per_month() -> go.Figure:
        """Independent of the Payment Date filtering below — see its own
        computation for why it needs a different date column entirely."""
        spent_source = df.copy()
        spent_source["Transaction Date"] = pd.to_datetime(
            spent_source["Transaction Date"], errors="coerce", format=DATE_FORMAT
        )
        spent_source = spent_source.dropna(subset=["Transaction Date"])
        if start_date:
            spent_source = spent_source[
                spent_source["Transaction Date"] >= pd.to_datetime(start_date)
            ]
        if end_date:
            spent_source = spent_source[
                spent_source["Transaction Date"] <= pd.to_datetime(end_date)
            ]
        spent_source = spent_source[spent_source["Amount"] < 0].copy()
        spent_source["Amount"] = spent_source["Amount"].abs()
        spent_totals = charts.purchase_totals_by_month(spent_source, "Transaction Date")
        return charts.monthly_bar(
            spent_totals, "Transaction Date", "Amount spent per month", config.series_red
        )

    if df.empty:
        blank = charts.empty_figure("No transactions recorded yet")
        return (*[blank] * 4, _spent_per_month(), *[blank] * 2)

    filtered = df.copy()
    filtered["Payment Date"] = pd.to_datetime(
        filtered["Payment Date"], errors="coerce", format=DATE_FORMAT
    )
    filtered = filtered.dropna(subset=["Payment Date"])
    if start_date:
        filtered = filtered[filtered["Payment Date"] >= pd.to_datetime(start_date)]
    if end_date:
        filtered = filtered[filtered["Payment Date"] <= pd.to_datetime(end_date)]
    filtered["Payment Date"] = filtered["Payment Date"].dt.strftime(DATE_FORMAT)

    # "Amount spent per month" is computed once, here, regardless of whether
    # the Payment Date window below turns out empty: it answers "what did
    # buying this cost in total", so an installment purchase counts once, in
    # the month it was made, at its full value — never spread thin across
    # whichever of its installments happen to fall inside a Payment Date
    # window built for a different question.
    spent_fig = _spent_per_month()

    if filtered.empty:
        blank = charts.empty_figure("No data in the selected period")
        return (*[blank] * 4, spent_fig, *[blank] * 2)

    expenses = filtered[filtered["Amount"] < 0].copy()
    expenses["Amount"] = expenses["Amount"].abs()

    # Only the category breakdown drops flagged records.
    category_expenses = expenses[expenses["Ignore Entry"] == 0]

    installment_index = expenses["Installment"].map(parse_installment)
    first_installments = expenses[
        installment_index.map(lambda p: p is not None and p[0] == 1)
    ]
    last_installments = expenses[
        installment_index.map(lambda p: p is not None and p[0] == p[1])
    ]

    return (
        charts.monthly_bar(
            filtered, "Payment Date", "Cumulative balance", config.accent, cumulative=True
        ),
        charts.share_pie(expenses, "Payment Method", "Spending by Payment Method"),
        charts.share_pie(category_expenses, "Category", "Spending by Category"),
        charts.monthly_bar(expenses, "Payment Date", "Amount paid per month", config.series_red),
        spent_fig,
        charts.monthly_bar(
            last_installments, "Payment Date", "Finishing payments", config.series_green
        ),
        charts.monthly_bar(
            first_installments, "Payment Date", "Starting payments", config.series_yellow
        ),
    )


# -----------------------------------------------------------------------------
# Callbacks: AI insight
# -----------------------------------------------------------------------------

@app.callback(
    Output("ai-comment", "children"),
    Output("ai-status", "children"),
    Input("update-ai-comment-btn", "n_clicks"),
    State("date-end", "date"),
    prevent_initial_call=True,
)
def update_ai_comment(_n_clicks, end_date):
    """Generate the written commentary for the trailing twelve months."""
    payment_method_types = {
        name: info["type"] for name, info in get_payment_methods().items()
    }
    metrics = compute_window_metrics(
        load_transactions(), end_date=end_date, payment_method_types=payment_method_types
    )
    insight = get_insight(metrics)
    text = f"{insight.text}\n\n_Last update: {current_timestamp()}_"
    return text, html.Span(f"Status: {insight.status.value}")


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    if config.open_browser:
        import threading
        import webbrowser

        url = f"http://localhost:{config.port}"
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    logger.info("Starting Dash server on http://%s:%d", config.host, config.port)
    logger.info("Ollama endpoint: %s (model: %s)", config.ollama_url, config.ollama_model)
    app.run(debug=config.debug, host=config.host, port=config.port)