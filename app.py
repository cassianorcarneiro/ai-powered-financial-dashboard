# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# AI POWERED FINANCIAL DASHBOARD
# CASSIANO RIBEIRO CARNEIRO
# V2
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

"""
Personal finance dashboard built with Dash + Plotly + a local Ollama model.

The app reads transactions from a CSV file, lets the user manage payment methods
and add new (optionally installment-based) records, and renders aggregated charts
plus an AI-generated insight summarizing the last 12 months of activity.
"""

# Frameworks imports

import json
import logging
import os
import threading
import time
import uuid
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import dash_auth
import dash_bootstrap_components as dbc
import matplotlib
import matplotlib.colors as mcolors
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
from dash import Dash, Input, Output, State, callback_context, dash_table, dcc, html
from dateutil.relativedelta import relativedelta

# Custom modules import

from config import Config as config

# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# Logging
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("financial_dashboard")

# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# Paths
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

BASE_DIR = Path(__file__).resolve().parent
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
PAYMENT_METHODS_PATH = BASE_DIR / config.payment_methods_db
CSV_PATH = BASE_DIR / config.csv_db
CATEGORIES_PATH = BASE_DIR / config.categories_db
LOADING_PAGE_PATH = BASE_DIR / "loading.html"

# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# Reusable style dictionaries (extracted from layout to avoid duplication)
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

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

CHART_AXIS_X = dict(
    showgrid=False,
    gridcolor=config.gray_1,
    gridwidth=1.0,
)

CHART_AXIS_Y = dict(
    gridcolor=config.gray_1,
    gridwidth=1.0,
    zerolinecolor=config.gray_3,
    zerolinewidth=3.0,
)

# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# AI-related functions
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

def compute_last12m_metrics(df: pd.DataFrame, end_date: str | None) -> dict[str, Any]:
    """Compute aggregated metrics over the trailing 12 months ending at `end_date`."""
    if df.empty:
        return {"has_data": False}

    d = df.copy()
    d["Payment Date"] = pd.to_datetime(d["Payment Date"], errors="coerce", format="%Y-%m-%d")
    d = d.dropna(subset=["Payment Date"])

    data_max = d["Payment Date"].max()
    end_ts = min(pd.to_datetime(end_date), data_max) if end_date else data_max
    start_ts = end_ts - relativedelta(months=12)

    w = d[(d["Payment Date"] > start_ts) & (d["Payment Date"] <= end_ts)].copy()
    w = w[w["Ignore Entry"] == 0]

    if w.empty:
        return {"has_data": False}

    # Convention: expenses are Amount < 0, income is Amount > 0
    income = w.loc[w["Amount"] > 0, "Amount"].sum()
    expense = w.loc[w["Amount"] < 0, "Amount"].abs().sum()
    net = income - expense

    # Monthly aggregates
    w["month"] = w["Payment Date"].dt.to_period("M").dt.to_timestamp()
    monthly_income = w[w["Amount"] > 0].groupby("month")["Amount"].sum()
    monthly_expense = (
        w[w["Amount"] < 0]
        .groupby("month")["Amount"]
        .apply(lambda s: s.abs().sum())
    )

    # Reindex over the full month range to fill gaps with zero
    if len(monthly_expense):
        idx_min, idx_max = monthly_expense.index.min(), monthly_expense.index.max()
    else:
        idx_min = idx_max = w["month"].min()
    all_months = pd.date_range(start=idx_min, end=idx_max, freq="MS")
    monthly_income = monthly_income.reindex(all_months, fill_value=0.0)
    monthly_expense = monthly_expense.reindex(all_months, fill_value=0.0)

    # Trend: average of last 3 months vs. previous 3
    def avg_tail(series: pd.Series, n: int) -> float:
        if len(series) < n:
            return float(series.mean()) if len(series) else 0.0
        return float(series.tail(n).mean())

    exp_last3 = avg_tail(monthly_expense, 3)
    exp_prev3 = avg_tail(monthly_expense.iloc[:-3], 3)
    exp_trend = (exp_last3 - exp_prev3) / exp_prev3 if exp_prev3 > 0 else None

    # Volatility (coefficient of variation) of monthly expenses
    exp_mean = float(monthly_expense.mean())
    exp_std = float(monthly_expense.std(ddof=0)) if len(monthly_expense) else 0.0
    exp_cv = (exp_std / exp_mean) if exp_mean > 0 else None

    # Top categories and payment methods (expenses only)
    cat = w[w["Amount"] < 0].copy()
    cat["abs_amount"] = cat["Amount"].abs()
    top_cat = (
        cat.groupby("Category")["abs_amount"].sum().sort_values(ascending=False).head(5)
    ).to_dict()

    pm = w[w["Amount"] < 0].copy()
    pm["abs_amount"] = pm["Amount"].abs()
    top_pm = (
        pm.groupby("Payment Method")["abs_amount"].sum().sort_values(ascending=False).head(5)
    ).to_dict()

    # Largest single expense
    largest_exp = None
    if not cat.empty:
        r = cat.loc[cat["abs_amount"].idxmax()]
        largest_exp = {
            "label": str(r.get("Label", "")),
            "category": str(r.get("Category", "")),
            "amount": float(r["abs_amount"]),
            "payment_date": r["Payment Date"].strftime("%Y-%m-%d"),
        }

    # Share of expenses paid in installments
    inst = w[w["Amount"] < 0].copy()
    inst["Installment"] = inst["Installment"].astype(str)
    is_installment = inst["Installment"].str.contains("/") & inst["Installment"].str.split("/").apply(
        lambda x: int(x[1]) > 1 if len(x) == 2 and x[1].isdigit() else False
    )
    installment_share = (
        float(inst.loc[is_installment, "Amount"].abs().sum() / expense) if expense > 0 else None
    )

    return {
        "has_data": True,
        "window": {"start": start_ts.strftime("%Y-%m-%d"), "end": end_ts.strftime("%Y-%m-%d")},
        "totals": {"income": float(income), "expense": float(expense), "net": float(net)},
        "monthly": {
            "expense_mean": exp_mean,
            "expense_cv": exp_cv,
            "expense_trend_3m": exp_trend,
        },
        "top": {"categories": top_cat, "payment_methods": top_pm},
        "largest_expense": largest_exp,
        "installment_share": installment_share,
        "months_count": int(len(monthly_expense)),
    }


def build_ai_prompt(metrics: dict[str, Any]) -> str:
    """Build the prompt sent to the local LLM for generating an insight comment."""
    return (
        "You are a personal financial analyst. Generate a short and objective comment (8–12 lines), "
        "based ONLY on the aggregated metrics below.\n\n"
        "Rules:\n"
        "- Do not invent numbers.\n"
        "- The entries are in Brazilian Reais.\n"
        "- Highlight: net balance, recent spending trend, largest categories, volatility, and observations about installments.\n"
        "- End with 3 numbered practical actions.\n\n"
        f"METRICS (JSON):\n{json.dumps(metrics, ensure_ascii=False)}\n"
    )


def ollama_generate(model: str, prompt: str, timeout_s: int = config.ollama_timeout) -> str:
    """Call the local Ollama generate endpoint with a single retry on timeout."""
    url = f"{OLLAMA_URL}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2, "top_p": 0.9},
    }

    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            r = requests.post(url, json=payload, timeout=timeout_s)
            r.raise_for_status()
            return (r.json().get("response") or "").strip()
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectTimeout) as e:
            last_exc = e
            logger.warning("Ollama timeout on attempt %d: %s", attempt + 1, e)
            time.sleep(1.0)  # small backoff
        except requests.exceptions.RequestException:
            raise
    assert last_exc is not None
    raise last_exc


def get_ai_comment(metrics: dict[str, Any], model: str = "llama3.2:3b") -> tuple[str, str]:
    """Generate an AI insight or fall back to a deterministic summary on failure."""
    if not metrics.get("has_data"):
        return ("No data", "Not enough data in the last 12 months to generate a comment.")

    prompt = build_ai_prompt(metrics)
    try:
        text = ollama_generate(model, prompt)
        if not text:
            return ("Failure", "The model did not return any text.")
        return ("OK", text)

    except Exception as e:
        # Fallback: deterministic summary so the dashboard never breaks
        logger.error("Ollama call failed, returning fallback summary: %s", e)
        t = metrics["totals"]
        w = metrics["window"]
        fallback = (
            f"**Summary (fallback, without AI)**\n\n"
            f"- Window: {w['start']} to {w['end']}\n"
            f"- Income: {t['income']:.2f}\n"
            f"- Expenses: {t['expense']:.2f}\n"
            f"- Net balance: {t['net']:.2f}\n\n"
            f"Error calling Ollama: `{type(e).__name__}`"
        )
        return ("Offline", fallback)

# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# General helpers
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

def get_datetime(timezone_str: str) -> datetime | str:
    """Return a timezone-aware current datetime, or an error message if the zone is invalid."""
    try:
        return datetime.now(ZoneInfo(timezone_str))
    except ZoneInfoNotFoundError:
        return f"Error: The timezone '{timezone_str}' does not exist."


def load_categories() -> pd.DataFrame:
    """Load and alphabetize the list of expense categories."""
    df_categories = pd.read_csv(CATEGORIES_PATH, sep=";", encoding="utf-8-sig")
    return df_categories.sort_values(by=["Name"], ascending=[True])


def get_categories() -> list[str]:
    """Return category names as a sorted list."""
    return sorted(load_categories()["Name"].tolist())


def load_payment_methods() -> pd.DataFrame:
    """Load payment methods sorted by name (and Type descending)."""
    df = pd.read_csv(PAYMENT_METHODS_PATH, sep=";", encoding="utf-8-sig")
    return df.sort_values(by=["Name", "Type"], ascending=[True, False])


def save_payment_methods(df_payment_methods: pd.DataFrame) -> None:
    """Persist payment methods, dropping rows with empty required fields."""
    cleaned = df_payment_methods.copy()
    # Drop rows with empty Name or Type — they would crash get_payment_methods later
    cleaned = cleaned[cleaned["Name"].astype(str).str.strip().ne("")]
    cleaned = cleaned[cleaned["Type"].astype(str).str.strip().ne("")]
    cleaned.to_csv(PAYMENT_METHODS_PATH, sep=";", index=False, encoding="utf-8-sig")


def get_payment_methods(name: str = "") -> dict[str, Any]:
    """Return all payment methods as a dict, or a single one if `name` is given."""
    df = load_payment_methods()
    payment_methods: dict[str, Any] = {}
    for _, row in df.iterrows():
        # Coerce day fields safely; debit accounts may legitimately have NaN here
        try:
            close_day = int(row["Close Date"]) if pd.notna(row["Close Date"]) else None
            pay_day = int(row["Payment Date"]) if pd.notna(row["Payment Date"]) else None
        except (TypeError, ValueError):
            close_day, pay_day = None, None
        payment_methods[row["Name"]] = {
            "statement_close_day": close_day,
            "payment_day": pay_day,
            "type": row["Type"],
        }

    if name == "":
        return payment_methods
    return payment_methods[name]


def open_loading_page() -> None:
    """Open the local loading page in the default browser, if it exists."""
    if not LOADING_PAGE_PATH.exists():
        logger.info("Loading page not found at %s; skipping browser open.", LOADING_PAGE_PATH)
        return
    webbrowser.open(f"file://{LOADING_PAGE_PATH}")


def load_data() -> pd.DataFrame:
    """Load the full transactions CSV."""
    return pd.read_csv(CSV_PATH, sep=";", encoding="utf-8-sig")


def generate_installments(
    label: str,
    category: str,
    date_str: str,
    total_amount: float,
    installments: int,
    payment_method_name: str,
    ignore: int,
) -> list[dict[str, Any]]:
    """Generate one record per installment, with computed payment dates.

    For credit cards, the first payment date depends on whether the transaction
    happened before or after the statement close day. For debit, payment is
    immediate.

    Expenses must be passed as negative amounts; positive amounts are treated as
    income. The sign is preserved across all installments.
    """
    payment_method_info = get_payment_methods(payment_method_name)
    statement_close_day = payment_method_info["statement_close_day"]
    payment_day = payment_method_info["payment_day"]
    method_type = payment_method_info["type"]

    ignore = 1 if ignore == 1 else 0
    base_date = datetime.strptime(date_str, "%Y-%m-%d")

    if method_type == "Credit":
        if statement_close_day is None or payment_day is None:
            raise ValueError(
                f"Credit method '{payment_method_name}' is missing close/payment days."
            )
        transaction_day = base_date.day

        if payment_day > statement_close_day:
            if transaction_day <= statement_close_day:
                first_payment_date = base_date.replace(day=payment_day)
            else:
                first_payment_date = (base_date + relativedelta(months=1)).replace(day=payment_day)
        else:
            if transaction_day <= statement_close_day:
                first_payment_date = (base_date + relativedelta(months=1)).replace(day=payment_day)
            else:
                first_payment_date = (base_date + relativedelta(months=2)).replace(day=payment_day)

    elif method_type == "Debit":
        first_payment_date = base_date

    else:
        raise ValueError(f"Unknown payment method type: '{method_type}'")

    installment_amount = round(total_amount / installments, 2)
    transaction_hash = str(uuid.uuid4())
    record_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    records = []
    for p in range(1, installments + 1):
        payment_date = first_payment_date + relativedelta(months=p - 1)
        records.append({
            "Transaction Date": base_date.strftime("%Y-%m-%d"),
            "Payment Date": payment_date.strftime("%Y-%m-%d"),
            "Label": label,
            "Category": category,
            "Amount": installment_amount,
            "Installment": f"{p}/{installments}",
            "Payment Method": payment_method_name,
            "Hash": transaction_hash,
            "Record Timestamp": record_timestamp,
            "Ignore Entry": ignore,
        })
    return records


def fill_months(df: pd.DataFrame, date_column: str, value_column: str) -> pd.DataFrame:
    """Reindex a monthly DataFrame to ensure every month in the range is present."""
    if df.empty:
        return df
    all_months = pd.date_range(df[date_column].min(), df[date_column].max(), freq="MS")
    df = (
        df.set_index(date_column)
        .reindex(all_months, fill_value=0)
        .rename_axis(date_column)
        .reset_index()
    )
    df[date_column] = pd.to_datetime(df[date_column], dayfirst=False)
    df.columns = [date_column, value_column]
    return df


def generate_pastel_colors_auto(df: pd.DataFrame, column: str) -> list[str]:
    """Generate a pastel color palette sized to the number of unique values in `column`."""
    n = max(df[column].nunique(), 1)
    cmap = matplotlib.colormaps.get_cmap("Pastel1").resampled(n)
    return [mcolors.rgb2hex(cmap(i)) for i in range(n)]


def empty_figure(message: str = "No data available") -> go.Figure:
    """Return a placeholder figure used when a chart has no data."""
    fig = go.Figure()
    fig.add_annotation(
        text=message,
        xref="paper", yref="paper",
        x=0.5, y=0.5,
        showarrow=False,
        font=dict(size=16, color=config.blue_1),
    )
    fig.update_layout(
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        plot_bgcolor=config.blue_2,
    )
    return fig


# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# Chart factory (deduplicates the original 7 nearly-identical bar chart blocks)
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

def _apply_bar_layout(fig: go.Figure) -> go.Figure:
    """Apply the dashboard's standard bar-chart layout."""
    fig.update_layout(
        plot_bgcolor=config.blue_2,
        paper_bgcolor=config.blue_2,
        title_font_color=config.blue_1,
        font_color=config.blue_1,
        font_size=config.chart_fontsize_1,
        xaxis_title="Month/Year",
        yaxis_title="Amount",
        xaxis=CHART_AXIS_X,
        yaxis=CHART_AXIS_Y,
    )
    return fig


def _apply_pie_layout(fig: go.Figure) -> go.Figure:
    """Apply the dashboard's standard pie-chart layout."""
    fig.update_layout(
        plot_bgcolor=config.blue_2,
        paper_bgcolor=config.blue_2,
        title_font_color=config.blue_1,
        font_color=config.blue_1,
        font_size=config.chart_fontsize_1,
    )
    return fig


def make_monthly_bar(
    df: pd.DataFrame,
    date_column: str,
    title: str,
    color: str,
    cumulative: bool = False,
) -> go.Figure:
    """Aggregate `df` by month on `date_column` and render a bar chart."""
    if df.empty:
        return empty_figure()

    d = df.copy()
    d[date_column] = pd.to_datetime(d[date_column], format="%Y-%m-%d")
    d = d.groupby(d[date_column].dt.to_period("M"))["Amount"].sum().reset_index()
    d[date_column] = d[date_column].dt.to_timestamp()
    d = fill_months(d, date_column, "Amount")
    d["MonthYear"] = d[date_column].dt.strftime("%b %Y")

    if cumulative:
        d["Cumulative_Amount"] = d["Amount"].cumsum()
        y_col = "Cumulative_Amount"
    else:
        y_col = "Amount"

    if d.empty:
        return empty_figure()

    fig = px.bar(d, x="MonthYear", y=y_col, title=title, color_discrete_sequence=[color])
    return _apply_bar_layout(fig)


def make_pie(df: pd.DataFrame, group_col: str, title: str) -> go.Figure:
    """Aggregate `df` by `group_col` and render a pie chart of expense shares."""
    if df.empty:
        return empty_figure()

    palette = generate_pastel_colors_auto(df, group_col)
    fig = px.pie(
        df.groupby(group_col)["Amount"].sum().reset_index(),
        names=group_col,
        values="Amount",
        title=title,
        color_discrete_sequence=palette,
    )
    return _apply_pie_layout(fig)


def _filter_full_installments(df: pd.DataFrame, mode: str) -> pd.DataFrame:
    """Filter to rows that are part of a multi-installment purchase.

    Args:
        df:    Source DataFrame containing an "Installment" column like "p/n".
        mode:  Either "first" (rows where installment index == 1) or "last"
               (rows where installment index == total).
    """
    d = df.copy()
    d["Installment"] = d["Installment"].astype(str)
    d = d[d["Installment"].str.contains("/")]
    parts = d["Installment"].str.split("/")
    d = d[parts.apply(lambda x: len(x) == 2 and x[1].isdigit() and int(x[1]) > 1)]

    if d.empty:
        return d

    if mode == "first":
        d = d[d["Installment"].str.startswith("1/")]
    elif mode == "last":
        d = d[d.apply(
            lambda row: row["Installment"].split("/")[0] == row["Installment"].split("/")[1],
            axis=1,
        )]
    else:
        raise ValueError(f"Unknown installment filter mode: {mode}")
    return d


# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# Dashboard app
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

current_year = datetime.now().year
default_start_date = f"{current_year}-01-01"
default_end_date = f"{current_year}-12-31"

app = Dash(
    __name__,
    external_stylesheets=[dbc.themes.BOOTSTRAP, dbc.icons.FONT_AWESOME],
    title="Financial Control",
)

if config.request_password:
    auth = dash_auth.BasicAuth(app, config.valid_users)


def _icon_button(icon_class: str, button_id: str) -> dbc.Button:
    """Helper to build an icon button with the standard style."""
    return dbc.Button(html.I(className=icon_class), id=button_id, className="me-2", style=ICON_BUTTON_STYLE)


app.layout = html.Div([

    # Title
    html.H1(
        "Financial Control",
        className="text-center my-4 py-2",
        style={"backgroundColor": config.blue_3, "color": "white"},
    ),

    html.P(dbc.Container([

        dcc.Store(id="update-trigger", data=0),

        # AI Comment card
        dbc.Row([
            dbc.Col(
                dbc.Card(
                    dbc.CardBody([
                        dbc.Row([
                            dbc.Col(
                                html.Div("AI Insight (last 12 months)", style={"fontWeight": "bold"}),
                                width="auto",
                            ),
                            dbc.Col(html.Div(id="ai-status", style={"textAlign": "right"})),
                        ]),
                        html.Hr(),
                        dcc.Markdown(
                            id="ai-comment",
                            children="Click 'Generate AI Insight' to analyze your last 12 months.",
                            style={"whiteSpace": "pre-wrap"},
                        ),
                    ]),
                    style=CARD_BODY_STYLE,
                ),
                width=12,
            )
        ], className="mb-4"),

        # Filters (collapsible)
        dbc.Row([
            dbc.Col([
                dbc.Collapse(
                    dbc.Card(
                        dbc.CardBody([
                            dbc.Row([
                                dbc.Col([
                                    dbc.Label("Start Payment Date"),
                                    dbc.Input(
                                        id="date-start", type="date",
                                        value=default_start_date,
                                        style={"width": "100%"},
                                    ),
                                ], width=3, className="d-flex flex-column align-items-center justify-content-center"),

                                dbc.Col([
                                    dbc.Label("End Payment Date"),
                                    dbc.Input(
                                        id="date-end", type="date",
                                        value=default_end_date,
                                        style={"width": "100%"},
                                    ),
                                ], width=3, className="d-flex flex-column align-items-center justify-content-center"),
                            ])
                        ], style={**CARD_BODY_STYLE, "fontSize": config.fontsize_3}),
                        style=CARD_BODY_STYLE,
                    ),
                    id="filters-collapse",
                    is_open=False,
                    style={"width": "100%"},
                )
            ], width=24, className="ms-auto")
        ], className="my-4"),

        # Buttons
        dbc.Row([
            dbc.Col(_icon_button("fa-solid fa-filter", "toggle-filters"), width="auto"),
            dbc.Col(_icon_button("fa-solid fa-arrow-rotate-left", "reset-btn"), width="auto"),
            dbc.Col(_icon_button("fa fa-credit-card", "open-payment-methods-modal"), width="auto"),
            dbc.Col(_icon_button("fa fa-plus", "open-modal"), width="auto"),
            dbc.Col(
                dbc.Button("Generate AI Insight", id="update-ai-comment-btn",
                           className="me-2", style=ICON_BUTTON_STYLE),
                width="auto",
            ),
        ], className="my-4"),

        # New Record Modal
        dbc.Modal([
            dbc.ModalHeader("New Record", style=MODAL_HEADER_STYLE),
            dbc.ModalBody([
                dbc.Row([
                    dbc.Col([
                        dbc.Label("Transaction Date"),
                        dbc.Input(id="input-date", type="date"),
                    ], width=6, className="pe-2"),
                    dbc.Col([
                        dbc.Label("Amount"),
                        dbc.Input(id="input-amount", type="number", step="0.01"),
                    ], width=6, className="ps-2"),
                ], className="mb-3"),

                dbc.Label("Label"),
                dbc.Input(id="input-label", type="text", className="mb-3"),

                dbc.Label("Category"),
                dbc.Select(
                    id="input-category",
                    options=[{"label": m, "value": m} for m in get_categories()],
                    className="mb-3",
                ),

                dbc.Row([
                    dbc.Col([
                        dbc.Label("Payment Method"),
                        dbc.Select(
                            id="input-payment-method",
                            options=[{"label": m, "value": m} for m in get_payment_methods().keys()],
                            value=next(iter(get_payment_methods().keys()), None),
                        ),
                    ], width=6, className="pe-2"),
                    dbc.Col([
                        dbc.Label("Installments"),
                        dbc.Input(id="input-installments", type="number", min=1, value=1),
                    ], width=6, className="ps-2"),
                ], className="mb-4"),

                dbc.Checkbox(id="input-ignore", label="Ignore entry?", value=False, className="mb-3"),
            ], style=MODAL_BODY_STYLE),
            dbc.ModalFooter([
                dbc.Button("Insert", id="btn-save", style=PRIMARY_BUTTON_STYLE),
                dbc.Button("Cancel", id="btn-close", style=SECONDARY_BUTTON_STYLE),
            ], style={"backgroundColor": config.gray_1}),
        ], id="modal", is_open=False),

        # Payment Methods Modal
        dbc.Modal([
            dbc.ModalHeader("Manage Payment Methods", style=MODAL_HEADER_STYLE),
            dbc.ModalBody([
                dash_table.DataTable(
                    id="table-payment-methods",
                    columns=[
                        {"name": c, "id": c, "editable": True}
                        for c in ["Name", "Close Date", "Payment Date", "Type"]
                    ],
                    data=load_payment_methods().to_dict("records"),
                    editable=True, row_deletable=True,
                    style_table={"overflowX": "auto"},
                    style_header={"backgroundColor": config.blue_2, "color": "white", "fontWeight": "bold"},
                    style_cell={"backgroundColor": "white", "color": "black", "textAlign": "center"},
                ),
                dbc.Button(
                    "Add", id="btn-add-payment-method",
                    className="mt-2 float-end",
                    style={**ICON_BUTTON_STYLE, "color": "white"},
                ),
            ], style={"backgroundColor": config.gray_1, "padding": "30px"}),
            dbc.ModalFooter([
                dbc.Button("Save", id="btn-save-payment-methods", style=PRIMARY_BUTTON_STYLE),
                dbc.Button("Close", id="btn-close-payment-methods", style=SECONDARY_BUTTON_STYLE),
            ], style={"backgroundColor": config.gray_1}),
        ], id="modal-payment-methods", is_open=False, size="xl"),

        # Charts
        dbc.Row([dbc.Col(dcc.Graph(id="fig2"), width=6),
                 dbc.Col(dcc.Graph(id="fig3"), width=6)], className="mb-4"),
        dbc.Row([dbc.Col(dcc.Graph(id="fig4"), width=6),
                 dbc.Col(dcc.Graph(id="fig5"), width=6)], className="mb-4"),
        dbc.Row([dbc.Col(dcc.Graph(id="fig7"), width=6),
                 dbc.Col(dcc.Graph(id="fig6"), width=6)], className="mb-4"),
        dbc.Row([dbc.Col(dcc.Graph(id="fig1"), width=12)], className="mb-4"),

        # Delete buttons
        dbc.Row([
            dbc.Col(
                dbc.Button("Delete entry", id="btn-delete-hash-selected", style=ICON_BUTTON_STYLE),
                width="auto",
            ),
            dbc.Col(
                dbc.Button("Delete selected installments", id="btn-delete-selected", style=ICON_BUTTON_STYLE),
                width="auto",
            ),
        ], className="my-4"),

        # Main table
        dbc.Row([
            dbc.Col(
                dash_table.DataTable(
                    id="table-data",
                    columns=[],
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
                    filter_action="none",
                    sort_action="none",
                    page_action="none",
                    editable=False,
                ),
                width=12,
            )
        ], className="mb-4"),

    ], fluid=True, style={"backgroundColor": config.blue_3}))

], style={"backgroundColor": config.blue_3, "minHeight": "100vh", "padding": "20px"})

# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# Callbacks
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

# ----- Show/hide filters -----

@app.callback(
    Output("filters-collapse", "is_open"),
    Input("toggle-filters", "n_clicks"),
    prevent_initial_call=True,
)
def toggle_filters(n_clicks):
    return n_clicks % 2 == 1


# ----- Reset filters -----

@app.callback(
    Output("date-start", "value"),
    Output("date-end", "value"),
    Input("reset-btn", "n_clicks"),
    prevent_initial_call=True,
)
def reset_filters(n_clicks):
    return default_start_date, default_end_date


# ----- Toggle Payment Methods modal and refresh dropdown -----

@app.callback(
    [Output("modal-payment-methods", "is_open"),
     Output("input-payment-method", "options")],
    [Input("open-payment-methods-modal", "n_clicks"),
     Input("btn-close-payment-methods", "n_clicks"),
     Input("btn-save-payment-methods", "n_clicks")],
    [State("modal-payment-methods", "is_open"),
     State("table-payment-methods", "data")],
)
def toggle_payment_methods_modal(open_click, close_click, save_click, is_open, table_data):
    trigger = callback_context.triggered_id
    if trigger == "btn-save-payment-methods" and table_data:
        save_payment_methods(pd.DataFrame(table_data))

    options = [{"label": m, "value": m} for m in get_payment_methods().keys()]
    if open_click or close_click or save_click:
        return not is_open, options
    return is_open, options


@app.callback(
    Output("table-payment-methods", "data"),
    [Input("btn-add-payment-method", "n_clicks")],
    [State("table-payment-methods", "data"),
     State("table-payment-methods", "columns")],
    prevent_initial_call=True,
)
def add_payment_method(n_clicks, rows, columns):
    rows.append({c["id"]: "" for c in columns})
    return rows


# ----- Toggle New Record modal -----

@app.callback(
    Output("modal", "is_open"),
    [Input("open-modal", "n_clicks"),
     Input("btn-close", "n_clicks")],
    [State("modal", "is_open")],
)
def toggle_modal(open_click, close_click, is_open):
    if open_click or close_click:
        return not is_open
    return is_open


# ----- Save / delete / refresh charts and table -----

@app.callback(
    [Output("fig1", "figure"), Output("fig2", "figure"), Output("fig3", "figure"),
     Output("fig4", "figure"), Output("fig5", "figure"), Output("fig6", "figure"),
     Output("fig7", "figure"),
     Output("table-data", "data"), Output("table-data", "columns"),
     Output("input-label", "value"), Output("input-category", "value"),
     Output("input-date", "value"), Output("input-amount", "value"),
     Output("input-installments", "value"), Output("input-ignore", "value"),
     Output("table-data", "selected_rows")],
    [Input("btn-save", "n_clicks"),
     Input("btn-delete-hash-selected", "n_clicks"),
     Input("btn-delete-selected", "n_clicks"),
     Input("update-trigger", "data"),
     Input("date-start", "value"), Input("date-end", "value")],
    [State("input-label", "value"), State("input-category", "value"),
     State("input-date", "value"), State("input-amount", "value"),
     State("input-installments", "value"), State("input-payment-method", "value"),
     State("input-ignore", "value"),
     State("table-data", "selected_rows"), State("table-data", "data")],
    prevent_initial_call=False,
)
def update_all(
    save_click, delete_click_hash, delete_click, update_trigger, start_date, end_date,
    label, category, date, amount, installments, payment_method, ignore,
    selected_rows, table_data,
):
    trigger = callback_context.triggered_id
    df = load_data()

    # ----- Save new record -----
    if trigger == "btn-save" and all([label, category, date, amount, installments, payment_method]):
        try:
            records = generate_installments(
                label, category, date, float(amount), int(installments), payment_method, ignore,
            )
            df = pd.concat([df, pd.DataFrame(records)], ignore_index=True)
            df.to_csv(CSV_PATH, sep=";", index=False, encoding="utf-8-sig")
            df = load_data()
            logger.info("Inserted %d installment records for '%s'.", len(records), label)
        except (ValueError, KeyError) as e:
            logger.error("Failed to insert record: %s", e)
        clear_label, clear_category, clear_date, clear_amount, clear_ignore, clear_installments = (
            "", None, None, None, None, False
        )
    else:
        clear_label, clear_category, clear_date, clear_amount, clear_ignore, clear_installments = (
            None, None, None, None, None, False
        )

    # ----- Delete by hash (whole purchase, all installments) -----
    if trigger == "btn-delete-hash-selected" and selected_rows:
        df_table = pd.DataFrame(table_data)
        hashes_to_delete = df_table.iloc[selected_rows]["Hash"].tolist()
        df = df[~df["Hash"].isin(hashes_to_delete)]
        df.to_csv(CSV_PATH, sep=";", index=False, encoding="utf-8-sig")
        logger.info("Deleted purchases with hashes: %s", hashes_to_delete)

    # ----- Delete only selected installments -----
    if trigger == "btn-delete-selected" and selected_rows:
        df_table = pd.DataFrame(table_data)
        rows_to_delete = df_table.iloc[selected_rows][["Hash", "Payment Date"]]
        rows_to_delete["Payment Date"] = pd.to_datetime(rows_to_delete["Payment Date"], format="%d/%m/%Y")
        df = df[
            ~df.set_index(["Hash", "Payment Date"]).index.isin(
                rows_to_delete.set_index(["Hash", "Payment Date"]).index
            )
        ]
        df.to_csv(CSV_PATH, sep=";", index=False, encoding="utf-8-sig")
        logger.info("Deleted %d individual installments.", len(rows_to_delete))

    # ----- Build the table view -----
    columns_to_show = [
        "Record Timestamp", "Transaction Date", "Payment Date", "Label",
        "Category", "Amount", "Payment Method", "Hash",
    ]
    df_table = df.copy()

    df_table["Record Timestamp"] = pd.to_datetime(
        df_table["Record Timestamp"], errors="coerce", format="%Y-%m-%d %H:%M:%S"
    )
    df_table["Transaction Date"] = pd.to_datetime(
        df_table["Transaction Date"], errors="coerce", format="%Y-%m-%d"
    )
    df_table["Payment Date"] = pd.to_datetime(
        df_table["Payment Date"], errors="coerce", format="%Y-%m-%d"
    )

    df_table["Record Timestamp"] = df_table["Record Timestamp"].dt.strftime("%d/%m/%Y %H:%M:%S")
    df_table["Transaction Date"] = df_table["Transaction Date"].dt.strftime("%d/%m/%Y")
    df_table["Payment Date"] = df_table["Payment Date"].dt.strftime("%d/%m/%Y")

    df_table = df_table.sort_values("Record Timestamp", ascending=False).head(40)
    df_table = df_table[columns_to_show]
    columns = [{"name": col, "id": col} for col in df_table.columns]
    data = df_table.to_dict("records")

    # ----- Filter by selected date range for charts -----
    df_filt = df.copy()
    df_filt["Payment Date"] = pd.to_datetime(df_filt["Payment Date"], errors="coerce", format="%Y-%m-%d")
    if start_date:
        df_filt = df_filt[df_filt["Payment Date"] >= pd.to_datetime(start_date)]
    if end_date:
        df_filt = df_filt[df_filt["Payment Date"] <= pd.to_datetime(end_date)]

    if df_filt.empty:
        empty = empty_figure()
        return (empty, empty, empty, empty, empty, empty, empty,
                data, columns, clear_label, clear_category, clear_date, clear_amount,
                clear_installments, clear_ignore, [])

    # Common preprocessing for expense-only views
    not_ignored = df_filt[df_filt["Ignore Entry"] == 0].copy()
    expenses = not_ignored[not_ignored["Amount"] < 0].copy()
    expenses["Amount"] = expenses["Amount"].abs()

    # Chart 1 — cumulative balance over time (income + expenses)
    fig1 = make_monthly_bar(
        not_ignored, "Payment Date",
        title="Cumulative amount",
        color=config.blue_1,
        cumulative=True,
    )

    # Chart 2 — spending by payment method
    fig2 = make_pie(expenses, "Payment Method", title="Spending by Payment Method")

    # Chart 3 — spending by category
    fig3 = make_pie(expenses, "Category", title="Spending by Category")

    # Chart 4 — amount paid per month (BUG FIX: now respects Ignore Entry)
    fig4 = make_monthly_bar(
        expenses, "Payment Date",
        title="Amount paid per month",
        color=config.red_1,
    )

    # Chart 5 — amount spent per month (by Transaction Date)
    fig5 = make_monthly_bar(
        expenses, "Transaction Date",
        title="Amount spent per month",
        color=config.red_1,
    )

    # Chart 6 — last installments (purchases finishing payment this month)
    last_installments = _filter_full_installments(expenses, mode="last")
    fig6 = make_monthly_bar(
        last_installments, "Payment Date",
        title="Finishing payments",
        color=config.green_1,
    )

    # Chart 7 — first installments (purchases starting payment this month)
    first_installments = _filter_full_installments(expenses, mode="first")
    fig7 = make_monthly_bar(
        first_installments, "Payment Date",
        title="Starting payments",
        color=config.yellow_1,
    )

    return (
        fig1, fig2, fig3, fig4, fig5, fig6, fig7,
        data, columns,
        clear_label, clear_category, clear_date, clear_amount, clear_installments, clear_ignore,
        [],
    )


# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# AI insight callback
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

@app.callback(
    Output("ai-comment", "children"),
    Output("ai-status", "children"),
    Input("update-ai-comment-btn", "n_clicks"),
    State("date-start", "value"),
    State("date-end", "value"),
    prevent_initial_call=True,  # do not call Ollama on app startup
)
def update_ai_comment(n_clicks, start_date, end_date):
    df = load_data()
    df["Payment Date"] = pd.to_datetime(df["Payment Date"], errors="coerce", format="%Y-%m-%d")
    df = df.dropna(subset=["Payment Date"])

    if start_date:
        df = df[df["Payment Date"] >= pd.to_datetime(start_date)]
    if end_date:
        df = df[df["Payment Date"] <= pd.to_datetime(end_date)]

    metrics = compute_last12m_metrics(df, end_date=end_date)
    status, text = get_ai_comment(metrics, model=config.ollama_model)

    # Build the timestamp string outside the f-string to avoid nested-quote pitfalls
    now_dt = get_datetime(config.timezone)
    if isinstance(now_dt, datetime):
        timestamp = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    else:
        timestamp = str(now_dt)
    text += f"\n\nLast update: {timestamp}"

    return text, f"Status: {status}"


# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# Entry point
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

if __name__ == "__main__":
    # Open the loading splash in a background thread; the main thread starts the server
    threading.Thread(target=open_loading_page, daemon=True).start()
    time.sleep(1)
    logger.info("Starting Dash server on http://0.0.0.0:8050")
    app.run(debug=True, host="0.0.0.0", port=8050, use_reloader=False)