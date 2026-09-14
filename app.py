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
import uuid
from datetime import datetime
from typing import Any

import dash
import dash_bootstrap_components as dbc
import pandas as pd
from dash import Dash, Input, Output, State, callback_context, html, no_update
from dateutil.relativedelta import relativedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import charts
import layout as ui
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

def compute_first_payment_date(
    base_date: datetime,
    method: dict[str, Any],
    method_name: str,
) -> datetime:
    """Resolve when the first installment of a purchase is actually paid.

    For credit methods the answer depends on whether the purchase landed before
    or after the statement closed, and on whether the payment day falls before
    or after the close day within the month. Debit methods are paid immediately.
    """
    method_type = method["type"]

    if method_type == "Debit":
        return base_date

    if method_type != "Credit":
        raise ValueError(f"Unknown payment method type: {method_type!r}")

    close_day = method["statement_close_day"]
    payment_day = method["payment_day"]
    if close_day is None or payment_day is None:
        raise ValueError(
            f"Credit method {method_name!r} is missing its close day or payment day."
        )

    closed_already = base_date.day > close_day
    # When the payment day precedes the close day it belongs to the following
    # month's cycle, which shifts every case one month further out.
    offset = 0 if payment_day > close_day else 1
    offset += 1 if closed_already else 0

    target = base_date + relativedelta(months=offset)
    return _safe_replace_day(target, payment_day)


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
) -> list[dict[str, Any]]:
    """Split a purchase into one record per installment.

    The sign of `total_amount` is preserved, so expenses stay negative. Rounding
    residue from the division is added to the final installment, which keeps the
    sum of the parts exactly equal to the original amount.
    """
    if installments < 1:
        raise ValueError("The number of installments must be at least 1.")

    method = get_payment_method(payment_method_name)
    base_date = datetime.strptime(date_str, DATE_FORMAT)
    first_payment = compute_first_payment_date(base_date, method, payment_method_name)

    per_installment = round(total_amount / installments, 2)
    residue = round(total_amount - per_installment * installments, 2)

    transaction_hash = str(uuid.uuid4())
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

    return view[ui.TABLE_COLUMNS + [_ISO_DATE_KEY, _HASH_KEY]].to_dict("records")


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

app = Dash(
    __name__,
    external_stylesheets=[dbc.themes.BOOTSTRAP, dbc.icons.FONT_AWESOME],
    title="Financial Control",
    update_title=None,
)
app.layout = ui.build_layout
server = app.server  # WSGI entry point used by gunicorn

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
        <meta name="theme-color" content="#343d46">
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
# Callbacks: filters
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

@app.callback(
    Output("modal", "is_open"),
    Output("input-category", "options", allow_duplicate=True),
    Output("input-payment-method", "options", allow_duplicate=True),
    Input("open-modal", "n_clicks"),
    Input("btn-close", "n_clicks"),
    State("modal", "is_open"),
    prevent_initial_call=True,
)
def toggle_record_modal(_open, _close, is_open):
    """Toggle the record modal, refreshing both dropdowns each time it opens."""
    return (
        (not is_open),
        [{"label": c, "value": c} for c in get_categories()],
        [{"label": m, "value": m} for m in get_payment_methods()],
    )


# -----------------------------------------------------------------------------
# Callbacks: insert and delete
# -----------------------------------------------------------------------------

@app.callback(
    Output("update-trigger", "data"),
    Output("record-feedback", "children"),
    Output("input-label", "value"),
    Output("input-category", "value"),
    Output("input-date", "date"),
    Output("input-amount", "value"),
    Output("input-installments", "value"),
    Output("input-ignore", "value"),
    Input("btn-save", "n_clicks"),
    State("input-label", "value"),
    State("input-category", "value"),
    State("input-date", "date"),
    State("input-amount", "value"),
    State("input-installments", "value"),
    State("input-payment-method", "value"),
    State("input-ignore", "value"),
    State("update-trigger", "data"),
    prevent_initial_call=True,
)
def insert_record(
    _n_clicks, label, category, date_value, amount, installments,
    payment_method, ignore, trigger_value,
):
    """Validate the form and append the resulting installment records."""
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
            *[no_update] * 6,
        )

    try:
        amount_value = parse_amount(amount)
    except ValueError:
        return no_update, alert("Amount must be a number."), *[no_update] * 6

    if amount_value == 0:
        return no_update, alert("Amount cannot be zero."), *[no_update] * 6

    try:
        installment_count = int(installments or 1)
    except (TypeError, ValueError):
        return no_update, alert("Installments must be a whole number."), *[no_update] * 6

    if installment_count < 1:
        return no_update, alert("Installments must be at least 1."), *[no_update] * 6

    try:
        records = generate_installments(
            label=str(label).strip(),
            category=str(category),
            date_str=str(date_value),
            total_amount=amount_value,
            installments=installment_count,
            payment_method_name=str(payment_method),
            ignore=bool(ignore),
        )
        append_transactions(records)
    except (ValueError, KeyError) as exc:
        logger.error("Failed to insert record: %s", exc)
        return no_update, alert(str(exc)), *[no_update] * 6

    logger.info("Inserted %d record(s) for %r.", len(records), label)
    message = alert(
        f"Added {len(records)} record(s) for '{label}'.", color="success"
    )
    # Amount resets to "0.00" rather than blank so the mask has a value to build
    # on from the first keystroke.
    return (trigger_value or 0) + 1, message, "", None, None, "0.00", 1, False


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

    if df.empty:
        blank = charts.empty_figure("No transactions recorded yet")
        return (*[blank] * 7,)

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

    if filtered.empty:
        blank = charts.empty_figure("No data in the selected period")
        return (*[blank] * 7,)

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
            filtered, "Payment Date", "Cumulative balance", config.blue_1, cumulative=True
        ),
        charts.share_pie(expenses, "Payment Method", "Spending by Payment Method"),
        charts.share_pie(category_expenses, "Category", "Spending by Category"),
        charts.monthly_bar(expenses, "Payment Date", "Amount paid per month", config.red_1),
        charts.monthly_bar(
            expenses, "Transaction Date", "Amount spent per month", config.red_1
        ),
        charts.monthly_bar(
            last_installments, "Payment Date", "Finishing payments", config.green_1
        ),
        charts.monthly_bar(
            first_installments, "Payment Date", "Starting payments", config.yellow_1
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
    metrics = compute_window_metrics(load_transactions(), end_date=end_date)
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
