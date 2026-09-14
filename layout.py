# =============================================================================
# AI POWERED FINANCIAL DASHBOARD
# Dash layout definition.
# =============================================================================

"""The component tree, kept separate from the callbacks that drive it."""

from __future__ import annotations

from datetime import date

import dash_bootstrap_components as dbc
import pandas as pd
from dash import dash_table, dcc, html

from config import Config as config
from storage import (
    get_categories,
    get_payment_methods,
    load_categories,
    load_payment_methods,
)

# -----------------------------------------------------------------------------
# Reusable style dictionaries
# -----------------------------------------------------------------------------

ICON_BUTTON_STYLE = {
    "backgroundColor": config.blue_2,
    "borderColor": config.blue_2,
    "color": config.blue_1,
    "fontSize": config.fontsize_1,
    "minHeight": "44px",
}

PRIMARY_BUTTON_STYLE = {
    "backgroundColor": config.blue_4,
    "borderColor": config.blue_4,
    "color": "white",
    "fontSize": config.fontsize_1,
}

SECONDARY_BUTTON_STYLE = {
    "backgroundColor": config.gray_2,
    "borderColor": config.gray_2,
    "color": "white",
    "fontSize": config.fontsize_1,
}

CARD_BODY_STYLE = {
    "backgroundColor": config.blue_2,
    "borderColor": config.blue_2,
    "color": config.blue_1,
    "fontSize": config.fontsize_1,
}

MODAL_HEADER_STYLE = {
    "backgroundColor": config.gray_1,
    "fontWeight": "bold",
    "fontSize": config.fontsize_2,
}

MODAL_BODY_STYLE = {
    "backgroundColor": config.gray_1,
    "fontWeight": "bold",
    "fontSize": config.fontsize_1,
}

TABLE_COLUMNS = [
    "Record Timestamp",
    "Transaction Date",
    "Payment Date",
    "Label",
    "Category",
    "Amount",
    "Installment",
    "Payment Method",
]


def default_date_range() -> tuple[str, str]:
    """Default filter range: the current calendar year."""
    year = date.today().year
    return f"{year}-01-01", f"{year}-12-31"


# Plotly's default modebar is built for mouse interaction. On touch devices it
# never appears via hover anyway, so hiding it by default (it still shows on
# desktop hover) reclaims vertical space without losing functionality there.
#
# "responsive" is deliberately absent. It makes Plotly derive the plot height
# from its container, and the dbc.Col around each graph is auto-sized, so the
# height stays indeterminate: on a callback-driven re-render Plotly could settle
# on a taller box than the row reserves and spill over the content below it.
# charts.py sets an explicit pixel height instead, which is deterministic.
_GRAPH_CONFIG = {
    "displayModeBar": "hover",
    "displaylogo": False,
    # Re-measures the plot when the container changes size, so a width captured
    # before the layout settled corrects itself instead of persisting until the
    # page is reloaded. This is safe alongside the pinned height in charts.py:
    # with `height` set explicitly, a resize can only adjust the width. It was
    # the earlier combination of this flag with an indeterminate height that let
    # charts grow past their row and overlap the table.
    "responsive": True,
    # Zoom stays disabled; see the axes' `fixedrange` in charts.py. These two
    # close the client-side input paths: scrollZoom covers the wheel and the
    # touch pinch, doubleClick covers the tap-to-reset that would re-autoscale.
    "scrollZoom": False,
    "doubleClick": False,
}


def _graph(graph_id: str) -> dcc.Graph:
    """A dcc.Graph with the shared mobile-friendly config applied.

    No height is set here on purpose. A percentage height only resolves against
    a parent with a definite height, and the dbc.Col wrapping each graph is
    auto-sized, so `height: 100%` leaves the container indeterminate. Plotly
    then re-measures it on every callback-driven re-render and can settle on a
    height that no longer matches the space the column reserves, which is what
    made the table overlap the charts after a delete. Plotly's own default
    height applies instead, and `responsive` keeps it adapting to width.
    """
    return dcc.Graph(id=graph_id, config=_GRAPH_CONFIG)


def _icon_button(icon_class: str, button_id: str, tooltip: str) -> html.Span:
    """Icon button with a tooltip, so the icon-only toolbar stays discoverable.

    Sized to at least 44x44px, the minimum touch target recommended by WCAG
    2.5.5 and by both Apple's and Google's platform guidelines, so the button
    is reliably tappable on a phone rather than just clickable with a mouse.
    """
    return html.Span(
        [
            dbc.Button(
                html.I(className=icon_class),
                id=button_id,
                className="me-2",
                style={**ICON_BUTTON_STYLE, "minWidth": "44px", "minHeight": "44px"},
            ),
            dbc.Tooltip(tooltip, target=button_id, placement="bottom"),
        ]
    )


def _ai_card() -> dbc.Card:
    """Panel holding the generated insight and its status."""
    return dbc.Card(
        dbc.CardBody(
            [
                dbc.Row(
                    [
                        dbc.Col(
                            html.Div(
                                "AI Insight (last 12 months)",
                                style={"fontWeight": "bold"},
                            ),
                            width="auto",
                        ),
                        dbc.Col(html.Div(id="ai-status", style={"textAlign": "right"})),
                    ]
                ),
                html.Hr(),
                dcc.Loading(
                    dcc.Markdown(
                        id="ai-comment",
                        children=(
                            "Select a period and choose **Generate AI Insight** to "
                            "analyze the last 12 months of activity."
                        ),
                        style={"whiteSpace": "pre-wrap"},
                    ),
                    type="default",
                    color=config.blue_1,
                ),
            ]
        ),
        style=CARD_BODY_STYLE,
    )


# The browser's native <input type="date"> renders in the locale of the user's
# operating system, which cannot be overridden from the page. dcc.DatePickerSingle
# draws its own calendar, so the display format is ours to set. Its value still
# travels as an ISO yyyy-mm-dd string, which is what the callbacks expect.
DATE_DISPLAY_FORMAT = "DD/MM/YYYY"


def _date_picker(
    picker_id: str,
    initial: str | None = None,
    in_modal: bool = False,
) -> dcc.DatePickerSingle:
    """Date field rendered in day/month/year regardless of browser locale.

    Inside a dbc.Modal the calendar would be clipped by the dialog's own
    stacking and overflow, so those pickers render the calendar in a portal
    attached to the document body instead.
    """
    return dcc.DatePickerSingle(
        id=picker_id,
        date=initial,
        display_format=DATE_DISPLAY_FORMAT,
        placeholder="DD/MM/YYYY",
        clearable=True,
        with_portal=in_modal,
        style={"width": "100%"},
    )


def _filters(start_date: str, end_date: str) -> dbc.Collapse:
    """Collapsible date-range filter."""
    return dbc.Collapse(
        dbc.Card(
            dbc.CardBody(
                dbc.Row(
                    [
                        dbc.Col(
                            [
                                dbc.Label("Start Payment Date"),
                                _date_picker("date-start", start_date),
                            ],
                            xs=12,
                            md=4,
                            lg=3,
                            className="mb-2",
                        ),
                        dbc.Col(
                            [
                                dbc.Label("End Payment Date"),
                                _date_picker("date-end", end_date),
                            ],
                            xs=12,
                            md=4,
                            lg=3,
                            className="mb-2",
                        ),
                    ]
                ),
                style={**CARD_BODY_STYLE, "fontSize": config.fontsize_3},
            ),
            style=CARD_BODY_STYLE,
        ),
        id="filters-collapse",
        is_open=False,
        style={"width": "100%"},
    )


def _new_record_modal() -> dbc.Modal:
    """Modal used to insert a transaction, optionally split into installments."""
    methods = list(get_payment_methods().keys())
    return dbc.Modal(
        [
            dbc.ModalHeader("New Record", style=MODAL_HEADER_STYLE),
            dbc.ModalBody(
                [
                    dbc.Alert(
                        "Enter expenses as negative amounts and income as positive amounts.",
                        color="secondary",
                        className="py-2",
                        style={"fontSize": config.fontsize_1},
                    ),
                    dbc.Row(
                        [
                            dbc.Col(
                                [
                                    dbc.Label("Transaction Date"),
                                    _date_picker("input-date", in_modal=True),
                                ],
                                xs=12,
                                md=6,
                                className="mb-2",
                            ),
                            dbc.Col(
                                [
                                    dbc.Label("Total Amount"),
                                    # Text rather than number: a number input
                                    # cannot hold a partially typed, formatted
                                    # value, which the right-to-left currency
                                    # mask in assets/input-masks.js relies on.
                                    # inputMode still raises the numeric keypad
                                    # on a phone. If the script fails to load,
                                    # this degrades to a plain field where
                                    # "-123.45" can simply be typed.
                                    dbc.Input(
                                        id="input-amount",
                                        type="text",
                                        inputMode="decimal",
                                        value="0.00",
                                        autoComplete="off",
                                    ),
                                ],
                                xs=12,
                                md=6,
                                className="mb-2",
                            ),
                        ],
                        className="mb-2",
                    ),
                    dbc.Label("Label"),
                    dbc.Input(id="input-label", type="text", className="mb-3"),
                    dbc.Label("Category"),
                    dbc.Select(
                        id="input-category",
                        options=[{"label": c, "value": c} for c in get_categories()],
                        className="mb-3",
                    ),
                    dbc.Row(
                        [
                            dbc.Col(
                                [
                                    dbc.Label("Payment Method"),
                                    dbc.Select(
                                        id="input-payment-method",
                                        options=[{"label": m, "value": m} for m in methods],
                                        value=next(iter(methods), None),
                                    ),
                                ],
                                xs=12,
                                md=6,
                                className="mb-2",
                            ),
                            dbc.Col(
                                [
                                    dbc.Label("Installments"),
                                    dbc.Input(
                                        id="input-installments",
                                        type="number",
                                        min=1,
                                        step=1,
                                        value=1,
                                    ),
                                ],
                                xs=12,
                                md=6,
                                className="mb-2",
                            ),
                        ],
                        className="mb-3",
                    ),
                    dbc.Checkbox(
                        id="input-ignore",
                        label="Exclude this entry from charts and metrics",
                        value=False,
                        className="mb-2",
                    ),
                    html.Div(id="record-feedback"),
                ],
                style=MODAL_BODY_STYLE,
            ),
            dbc.ModalFooter(
                [
                    dbc.Button("Insert", id="btn-save", style=PRIMARY_BUTTON_STYLE),
                    dbc.Button("Cancel", id="btn-close", style=SECONDARY_BUTTON_STYLE),
                ],
                style={"backgroundColor": config.gray_1},
            ),
        ],
        id="modal",
        is_open=False,
    )


PAYMENT_METHOD_TYPE_OPTIONS = [
    {"label": "Credit", "value": "Credit"},
    {"label": "Debit", "value": "Debit"},
]


# These two editors deliberately use plain form controls instead of a
# dash_table.DataTable. A DataTable nested in a dbc.Modal fights the Bootstrap
# modal for keyboard and focus control: the modal's focus trap prevents the
# dropdown editor from opening, and swallows Backspace before the editable cell
# receives it. Native inputs have neither problem.


def _row_delete_button(kind: str, index: int) -> dbc.Button:
    """Small remove button for one editor row."""
    return dbc.Button(
        html.I(className="fa fa-trash"),
        id={"type": f"{kind}-delete", "index": index},
        color="danger",
        outline=True,
        style={"minHeight": "44px", "minWidth": "44px"},
    )


def payment_method_row(index: int, record: dict | None = None) -> dbc.Row:
    """One editable payment-method row."""
    record = record or {}

    def _day(value) -> str:
        """Render a stored day as a plain integer string, blank when absent."""
        try:
            if value in (None, "") or pd.isna(value):
                return ""
            return str(int(float(value)))
        except (TypeError, ValueError):
            return ""

    return dbc.Row(
        [
            dbc.Col(
                dbc.Input(
                    id={"type": "pm-name", "index": index},
                    type="text",
                    value=record.get("Name", ""),
                    placeholder="Name",
                ),
                xs=12, md=4, className="mb-2",
            ),
            dbc.Col(
                dbc.Select(
                    id={"type": "pm-type", "index": index},
                    options=PAYMENT_METHOD_TYPE_OPTIONS,
                    value=record.get("Type") or None,
                    placeholder="Type",
                ),
                xs=12, md=3, className="mb-2",
            ),
            dbc.Col(
                dbc.Input(
                    id={"type": "pm-close", "index": index},
                    type="number", min=1, max=31, step=1,
                    value=_day(record.get("Close Date")),
                    placeholder="Close day",
                ),
                xs=6, md=2, className="mb-2",
            ),
            dbc.Col(
                dbc.Input(
                    id={"type": "pm-pay", "index": index},
                    type="number", min=1, max=31, step=1,
                    value=_day(record.get("Payment Date")),
                    placeholder="Pay day",
                ),
                xs=6, md=2, className="mb-2",
            ),
            dbc.Col(_row_delete_button("pm", index), xs=12, md=1, className="mb-2"),
        ],
        className="g-2 align-items-center",
    )


def category_row(index: int, record: dict | None = None) -> dbc.Row:
    """One editable category row."""
    record = record or {}
    return dbc.Row(
        [
            dbc.Col(
                dbc.Input(
                    id={"type": "cat-name", "index": index},
                    type="text",
                    value=record.get("Name", ""),
                    placeholder="Category name",
                ),
                xs=10, className="mb-2",
            ),
            dbc.Col(_row_delete_button("cat", index), xs=2, className="mb-2"),
        ],
        className="g-2 align-items-center",
    )


def build_payment_method_rows(records: list[dict]) -> list[dbc.Row]:
    """Render every payment-method row, numbered so pattern-matching stays stable."""
    return [payment_method_row(i, r) for i, r in enumerate(records)]


def build_category_rows(records: list[dict]) -> list[dbc.Row]:
    """Render every category row, numbered so pattern-matching stays stable."""
    return [category_row(i, r) for i, r in enumerate(records)]


def _editor_modal(
    modal_id: str,
    title: str,
    hint: str,
    container_id: str,
    feedback_id: str,
    add_button_id: str,
    save_button_id: str,
    close_button_id: str,
    header: dbc.Row | None,
    initial_rows: list[dbc.Row],
) -> dbc.Modal:
    """Shared shell for the two list editors."""
    body: list = [
        dbc.Alert(
            hint, color="secondary", className="py-2",
            style={"fontSize": config.fontsize_1},
        )
    ]
    if header is not None:
        body.append(header)
    body += [
        html.Div(id=container_id, children=initial_rows),
        html.Div(id=feedback_id, className="mt-2"),
        dbc.Button(
            [html.I(className="fa fa-plus me-2"), "Add"],
            id=add_button_id,
            className="mt-2",
            style={**ICON_BUTTON_STYLE, "color": "white"},
        ),
    ]

    return dbc.Modal(
        [
            dbc.ModalHeader(title, style=MODAL_HEADER_STYLE),
            dbc.ModalBody(body, style={"backgroundColor": config.gray_1, "padding": "24px"}),
            dbc.ModalFooter(
                [
                    dbc.Button("Save", id=save_button_id, style=PRIMARY_BUTTON_STYLE),
                    dbc.Button("Close", id=close_button_id, style=SECONDARY_BUTTON_STYLE),
                ],
                style={"backgroundColor": config.gray_1},
            ),
        ],
        id=modal_id,
        is_open=False,
        size="lg",
    )


def _payment_methods_modal() -> dbc.Modal:
    """Modal for creating, editing and removing payment methods."""
    header = dbc.Row(
        [
            dbc.Col(html.Small("Name"), xs=12, md=4),
            dbc.Col(html.Small("Type"), xs=12, md=3),
            dbc.Col(html.Small("Close day"), xs=6, md=2),
            dbc.Col(html.Small("Pay day"), xs=6, md=2),
            dbc.Col(width=1),
        ],
        className="g-2 d-none d-md-flex fw-bold",
    )
    return _editor_modal(
        modal_id="modal-payment-methods",
        title="Manage Payment Methods",
        hint=(
            "Credit methods need a statement close day and a payment day (1-31). "
            "Debit methods can leave both empty."
        ),
        container_id="pm-rows-container",
        feedback_id="payment-methods-feedback",
        add_button_id="btn-add-payment-method",
        save_button_id="btn-save-payment-methods",
        close_button_id="btn-close-payment-methods",
        header=header,
        initial_rows=build_payment_method_rows(
            load_payment_methods().to_dict("records")
        ),
    )


def _categories_modal() -> dbc.Modal:
    """Modal for creating, renaming and removing categories."""
    return _editor_modal(
        modal_id="modal-categories",
        title="Manage Categories",
        hint=(
            "Categories already used by existing records are not renamed "
            "automatically here; this only edits the selectable list."
        ),
        container_id="cat-rows-container",
        feedback_id="categories-feedback",
        add_button_id="btn-add-category",
        save_button_id="btn-save-categories",
        close_button_id="btn-close-categories",
        header=None,
        initial_rows=build_category_rows(load_categories().to_dict("records")),
    )


def _charts() -> list[dbc.Row]:
    """Chart grid, stacking to a single column on narrow screens."""
    pairs = [("fig-method-share", "fig-category-share"),
             ("fig-paid-monthly", "fig-spent-monthly"),
             ("fig-starting", "fig-finishing")]
    rows = [
        dbc.Row(
            [
                dbc.Col(_graph(left), xs=12, lg=6, className="mb-3"),
                dbc.Col(_graph(right), xs=12, lg=6, className="mb-3"),
            ],
            className="mb-2",
        )
        for left, right in pairs
    ]
    rows.append(
        dbc.Row([dbc.Col(_graph("fig-cumulative"), width=12)], className="mb-4")
    )
    return rows


def build_layout() -> html.Div:
    """Assemble the full page."""
    start_date, end_date = default_date_range()

    return html.Div(
        [
            html.H1(
                "Financial Control",
                className="text-center my-4 py-2",
                style={"backgroundColor": config.blue_3, "color": "white"},
            ),
            dbc.Container(
                [
                    dcc.Store(id="update-trigger", data=0),
                    dbc.Row(dbc.Col(_ai_card(), width=12), className="mb-4"),
                    dbc.Row(
                        dbc.Col(_filters(start_date, end_date), width=12),
                        className="my-3",
                    ),
                    dbc.Row(
                        [
                            dbc.Col(
                                _icon_button(
                                    "fa-solid fa-filter", "toggle-filters", "Show or hide filters"
                                ),
                                width="auto",
                            ),
                            dbc.Col(
                                _icon_button(
                                    "fa-solid fa-arrow-rotate-left",
                                    "reset-btn",
                                    "Reset the date range",
                                ),
                                width="auto",
                            ),
                            dbc.Col(
                                _icon_button(
                                    "fa fa-credit-card",
                                    "open-payment-methods-modal",
                                    "Manage payment methods",
                                ),
                                width="auto",
                            ),
                            dbc.Col(
                                _icon_button(
                                    "fa-solid fa-tags",
                                    "open-categories-modal",
                                    "Manage categories",
                                ),
                                width="auto",
                            ),
                            dbc.Col(
                                _icon_button("fa fa-plus", "open-modal", "Add a new record"),
                                width="auto",
                            ),
                            dbc.Col(
                                dbc.Button(
                                    "Generate AI Insight",
                                    id="update-ai-comment-btn",
                                    className="me-2",
                                    style=ICON_BUTTON_STYLE,
                                ),
                                width="auto",
                            ),
                        ],
                        className="my-4 g-2",
                    ),
                    _new_record_modal(),
                    _payment_methods_modal(),
                    _categories_modal(),
                    *_charts(),
                    dbc.Row(
                        [
                            dbc.Col(
                                dbc.Button(
                                    "Delete whole purchase",
                                    id="btn-delete-hash-selected",
                                    style=ICON_BUTTON_STYLE,
                                ),
                                width="auto",
                            ),
                            dbc.Col(
                                dbc.Button(
                                    "Delete selected installments",
                                    id="btn-delete-selected",
                                    style=ICON_BUTTON_STYLE,
                                ),
                                width="auto",
                            ),
                            dbc.Col(html.Div(id="delete-feedback"), width="auto"),
                        ],
                        className="my-4 g-2",
                    ),
                    dbc.Row(
                        dbc.Col(
                            dash_table.DataTable(
                                id="table-data",
                                columns=[{"name": c, "id": c} for c in TABLE_COLUMNS],
                                data=[],
                                page_size=20,
                                style_table={
                                    "overflowX": "auto",
                                    "minWidth": "100%",
                                    "WebkitOverflowScrolling": "touch",
                                },
                                # No fixed_columns here on purpose: the DataTable
                                # implements it by overlaying a second, absolutely
                                # positioned table whose height is measured in
                                # JavaScript. That measurement goes stale whenever a
                                # callback replaces the data, leaving the overlay
                                # sized for the previous row count and painted over
                                # the charts above. Plain horizontal scrolling is
                                # correct in every state.
                                style_header={
                                    "backgroundColor": config.blue_2,
                                    "color": config.blue_1,
                                    "fontSize": config.fontsize_1,
                                },
                                style_cell={
                                    "backgroundColor": config.blue_3,
                                    "color": config.blue_1,
                                    "textAlign": "center",
                                    "minWidth": "100px",
                                    "whiteSpace": "normal",
                                    "fontSize": config.fontsize_1,
                                },
                                row_selectable="multi",
                                selected_rows=[],
                                # Server-side: sorting the rendered dd/mm/yyyy
                                # and two-decimal strings would order them as
                                # text. See _sort_view in app.py.
                                sort_action="custom",
                                sort_by=[],
                                filter_action="native",
                                page_action="native",
                                editable=False,
                            ),
                            width=12,
                        ),
                        className="mb-4",
                    ),
                ],
                fluid=True,
                style={"backgroundColor": config.blue_3},
            ),
        ],
        style={"backgroundColor": config.blue_3, "minHeight": "100vh", "padding": "20px"},
    )
