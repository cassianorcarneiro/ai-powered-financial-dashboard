# =============================================================================
# AI POWERED FINANCIAL DASHBOARD
# Dash layout definition.
# =============================================================================

"""The component tree, kept separate from the callbacks that drive it."""

from __future__ import annotations

from datetime import date

import dash_bootstrap_components as dbc
from dash import dash_table, dcc, html

from config import Config as config
from storage import get_categories, get_payment_methods, load_payment_methods

# -----------------------------------------------------------------------------
# Reusable style dictionaries
# -----------------------------------------------------------------------------

ICON_BUTTON_STYLE = {
    "backgroundColor": config.blue_2,
    "borderColor": config.blue_2,
    "color": config.blue_1,
    "fontSize": config.fontsize_1,
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


def _icon_button(icon_class: str, button_id: str, tooltip: str) -> html.Span:
    """Icon button with a tooltip, so the icon-only toolbar stays discoverable."""
    return html.Span(
        [
            dbc.Button(
                html.I(className=icon_class),
                id=button_id,
                className="me-2",
                style=ICON_BUTTON_STYLE,
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
                                dbc.Input(
                                    id="date-start",
                                    type="date",
                                    value=start_date,
                                    style={"width": "100%"},
                                ),
                            ],
                            xs=12,
                            md=4,
                            lg=3,
                            className="mb-2",
                        ),
                        dbc.Col(
                            [
                                dbc.Label("End Payment Date"),
                                dbc.Input(
                                    id="date-end",
                                    type="date",
                                    value=end_date,
                                    style={"width": "100%"},
                                ),
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
                                    dbc.Input(id="input-date", type="date"),
                                ],
                                xs=12,
                                md=6,
                                className="mb-2",
                            ),
                            dbc.Col(
                                [
                                    dbc.Label("Total Amount"),
                                    dbc.Input(id="input-amount", type="number", step="0.01"),
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


def _payment_methods_modal() -> dbc.Modal:
    """Modal for editing the payment-method table in place."""
    return dbc.Modal(
        [
            dbc.ModalHeader("Manage Payment Methods", style=MODAL_HEADER_STYLE),
            dbc.ModalBody(
                [
                    dbc.Alert(
                        "Type must be either Credit or Debit. Credit methods require a "
                        "statement close day and a payment day (1-31); debit methods can "
                        "leave both empty.",
                        color="secondary",
                        className="py-2",
                        style={"fontSize": config.fontsize_1},
                    ),
                    dash_table.DataTable(
                        id="table-payment-methods",
                        columns=[
                            {"name": "Name", "id": "Name", "editable": True},
                            {
                                "name": "Close Date",
                                "id": "Close Date",
                                "editable": True,
                                "type": "numeric",
                            },
                            {
                                "name": "Payment Date",
                                "id": "Payment Date",
                                "editable": True,
                                "type": "numeric",
                            },
                            {
                                "name": "Type",
                                "id": "Type",
                                "editable": True,
                                "presentation": "dropdown",
                            },
                        ],
                        dropdown={
                            "Type": {
                                "options": [
                                    {"label": "Credit", "value": "Credit"},
                                    {"label": "Debit", "value": "Debit"},
                                ]
                            }
                        },
                        data=load_payment_methods().to_dict("records"),
                        editable=True,
                        row_deletable=True,
                        style_table={"overflowX": "auto"},
                        style_header={
                            "backgroundColor": config.blue_2,
                            "color": "white",
                            "fontWeight": "bold",
                        },
                        style_cell={
                            "backgroundColor": "white",
                            "color": "black",
                            "textAlign": "center",
                        },
                    ),
                    html.Div(id="payment-methods-feedback", className="mt-2"),
                    dbc.Button(
                        "Add row",
                        id="btn-add-payment-method",
                        className="mt-2 float-end",
                        style={**ICON_BUTTON_STYLE, "color": "white"},
                    ),
                ],
                style={"backgroundColor": config.gray_1, "padding": "30px"},
            ),
            dbc.ModalFooter(
                [
                    dbc.Button(
                        "Save", id="btn-save-payment-methods", style=PRIMARY_BUTTON_STYLE
                    ),
                    dbc.Button(
                        "Close", id="btn-close-payment-methods", style=SECONDARY_BUTTON_STYLE
                    ),
                ],
                style={"backgroundColor": config.gray_1},
            ),
        ],
        id="modal-payment-methods",
        is_open=False,
        size="xl",
    )


def _charts() -> list[dbc.Row]:
    """Chart grid, stacking to a single column on narrow screens."""
    pairs = [("fig-method-share", "fig-category-share"),
             ("fig-paid-monthly", "fig-spent-monthly"),
             ("fig-starting", "fig-finishing")]
    rows = [
        dbc.Row(
            [
                dbc.Col(dcc.Graph(id=left), xs=12, lg=6, className="mb-3"),
                dbc.Col(dcc.Graph(id=right), xs=12, lg=6, className="mb-3"),
            ],
            className="mb-2",
        )
        for left, right in pairs
    ]
    rows.append(
        dbc.Row([dbc.Col(dcc.Graph(id="fig-cumulative"), width=12)], className="mb-4")
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
                                style_table={"overflowX": "auto"},
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
                                sort_action="native",
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
