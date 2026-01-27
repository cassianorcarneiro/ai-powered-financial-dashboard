# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# AI POWERED FINANCIAL DASHBOARD
# CASSIANO RIBEIRO CARNEIRO
# V1
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

# Frameworks imports

import pandas as pd
from dash import Dash, html, dcc, Input, Output, State, callback_context
import dash_auth
import dash_bootstrap_components as dbc
import plotly.express as px
from datetime import datetime
from dateutil.relativedelta import relativedelta
import uuid
import os
from dash import dash_table
import webbrowser, time, threading
import matplotlib
import matplotlib.colors as mcolors
import plotly.graph_objects as go
import json
import requests
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Custom modules import

from config import Config as config

# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# Paths
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
PAYMENT_METHODS_PATH = os.path.join(os.path.dirname(__file__), config.payment_methods_db)
CSV_PATH = os.path.join(os.path.dirname(__file__), config.csv_db)
CATEGORIES_PATH = os.path.join(os.path.dirname(__file__), config.categories_db)

# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# Custom functions for IA functionality
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

def compute_last12m_metrics(df: pd.DataFrame, end_date: str | None) -> dict:

    if df.empty:
        return {"has_data": False}

    d = df.copy()
    d["Payment Date"] = pd.to_datetime(d["Payment Date"], errors="coerce", format="%Y-%m-%d")
    d = d.dropna(subset=["Payment Date"])

    data_max = d["Payment Date"].max()

    if end_date:
        end_ts = min(pd.to_datetime(end_date), data_max)
    else:
        end_ts = data_max

    start_ts = end_ts - relativedelta(months=12)
    w = d[(d["Payment Date"] > start_ts) & (d["Payment Date"] <= end_ts)].copy()
    w = w[w["Ignore Entry"] == 0]

    if w.empty:
        return {"has_data": False}

    # Convention for your dataset: expenses are Amount < 0; entries are Amount > 0

    income = w.loc[w["Amount"] > 0, "Amount"].sum()
    expense = w.loc[w["Amount"] < 0, "Amount"].abs().sum()
    net = income - expense

    # Monthly series of expenses and income

    w["month"] = w["Payment Date"].dt.to_period("M").dt.to_timestamp()
    monthly_income = w[w["Amount"] > 0].groupby("month")["Amount"].sum()
    monthly_expense = w[w["Amount"] < 0].groupby("month")["Amount"].apply(lambda s: s.abs().sum())

    # Complete months absent

    all_months = pd.date_range(start=monthly_expense.index.min() if len(monthly_expense) else w["month"].min(),
                               end=monthly_expense.index.max() if len(monthly_expense) else w["month"].max(),
                               freq="MS")
    monthly_income = monthly_income.reindex(all_months, fill_value=0.0)
    monthly_expense = monthly_expense.reindex(all_months, fill_value=0.0)

    # Simple trend: compares the average of the last 3 months vs. the previous 3 months

    def avg_tail(series: pd.Series, n: int) -> float:
        if len(series) < n:
            return float(series.mean()) if len(series) else 0.0
        return float(series.tail(n).mean())

    exp_last3 = avg_tail(monthly_expense, 3)
    exp_prev3 = avg_tail(monthly_expense.iloc[:-3], 3)
    exp_trend = (exp_last3 - exp_prev3) / exp_prev3 if exp_prev3 > 0 else None

    # Volatility (CV = std/mean) of monthly expenses

    exp_mean = float(monthly_expense.mean())
    exp_std = float(monthly_expense.std(ddof=0)) if len(monthly_expense) else 0.0
    exp_cv = (exp_std / exp_mean) if exp_mean > 0 else None

    # Top categories (expenses)

    cat = w[w["Amount"] < 0].copy()
    cat["abs_amount"] = cat["Amount"].abs()
    top_cat = (cat.groupby("Category")["abs_amount"].sum().sort_values(ascending=False).head(5)).to_dict()

    # Top methods (expenses)

    pm = w[w["Amount"] < 0].copy()
    pm["abs_amount"] = pm["Amount"].abs()
    top_pm = (pm.groupby("Payment Method")["abs_amount"].sum().sort_values(ascending=False).head(5)).to_dict()

    # Highest unit cost (per transaction)

    largest_exp = None
    if not cat.empty:
        r = cat.loc[cat["abs_amount"].idxmax()]
        largest_exp = {
            "label": str(r.get("Label", "")),
            "category": str(r.get("Category", "")),
            "amount": float(r["abs_amount"]),
            "payment_date": r["Payment Date"].strftime("%Y-%m-%d")
        }

    # Proxy for "installments" (based on the Installment field)

    inst = w[w["Amount"] < 0].copy()
    inst["Installment"] = inst["Installment"].astype(str)
    is_installment = inst["Installment"].str.contains("/") & inst["Installment"].str.split("/").apply(lambda x: int(x[1]) > 1 if len(x) == 2 and x[1].isdigit() else False)
    installment_share = float(inst.loc[is_installment, "Amount"].abs().sum() / expense) if expense > 0 else None

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

def build_ai_prompt(metrics: dict) -> str:

    m = metrics
    
    return (
        "You are a personal financial analyst. Generate a short and objective comment (8–12 lines), "
        "based ONLY on the aggregated metrics below.\n\n" #"in Brazilian Portuguese, ...
        "Rules:\n"
        "- Do not invent numbers.\n"
        "- The entries are in Brazilian Reais.\n"
        "- Highlight: net balance, recent spending trend, largest categories, volatility, and observations about installments.\n"
        "- End with 3 numbered practical actions.\n\n"
        f"METRICS (JSON):\n{json.dumps(m, ensure_ascii=False)}\n"
    )

def ollama_generate(model: str, prompt: str, timeout_s: int = config.ollama_timeout) -> str:
    url = f"{OLLAMA_URL}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2, "top_p": 0.9}
    }

    last_exc = None
    for attempt in range(2):
        try:
            r = requests.post(url, json=payload, timeout=timeout_s)
            r.raise_for_status()
            return (r.json().get("response") or "").strip()
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectTimeout) as e:
            last_exc = e
            time.sleep(1.0)  # pequeno backoff
        except Exception:
            raise
    raise last_exc

def get_ai_comment(metrics: dict, model: str = "llama3.2:3b") -> tuple[str, str]:
    
    if not metrics.get("has_data"):
        return ("No data", "Not enough data in the last 12 months to generate a comment.")

    prompt = build_ai_prompt(metrics)
    try:
        text = ollama_generate(model, prompt)
        if not text:
            return ("Failure", "The model did not return any text.")
        return ("OK", text)
    
    except Exception as e:
        # Fallback: comentário determinístico sem IA (não quebra o dashboard)
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
# General custom functions
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

def get_datetime(timezone_str):
    try:
        # Set the timezone based on the string
        timezone = ZoneInfo(timezone_str)
        
        # Get the current timezone-aware datetime
        current_datetime = datetime.now(timezone)
        return current_datetime
    
    except ZoneInfoNotFoundError:
        return f"Error: The timezone '{timezone_str}' does not exist."

def load_categories():
    df_categories = pd.read_csv(CATEGORIES_PATH, sep=";", encoding="utf-8-sig")
    df_categories = df_categories.sort_values(by=["Name"], ascending=[True])
    return df_categories

def get_categories():
    df_categories = load_categories()
    categories = []
    for _, row in df_categories.iterrows():
        categories.append(row["Name"])
    categories = sorted(categories)
    return categories

def load_payment_methods():
    df_payment_methods = pd.read_csv(PAYMENT_METHODS_PATH, sep=";", encoding="utf-8-sig")
    df_payment_methods = df_payment_methods.sort_values(by=["Name", "Type"], ascending=[True, False])
    return df_payment_methods

def save_payment_methods(df_payment_methods):
    df_payment_methods.to_csv(PAYMENT_METHODS_PATH, sep=";", index=False, encoding="utf-8-sig")

def get_payment_methods(name=''):
    df_payment_methods = load_payment_methods()
    payment_methods = {}
    for _, row in df_payment_methods.iterrows():

        if pd.notna(row["Close Date"]) and pd.notna(row["Payment Date"]):
            payment_methods[row["Name"]] = {
                "statement_close_day": int(row["Close Date"]),
                "payment_day": int(row["Payment Date"]),
                "type": row["Type"]
            }
        else:
            payment_methods[row["Name"]] = {
                "statement_close_day": row["Close Date"],
                "payment_day": row["Payment Date"],
                "type": row["Type"]
            }

    if name == '':
        return payment_methods
    return payment_methods[name]

def open_loading_page():
    path = os.path.join(os.path.dirname(__file__), "loading.html")
    local_url = f"file://{path}"
    webbrowser.open(local_url)

def load_data():
    df_sheet = pd.read_csv(CSV_PATH, sep=";", encoding="utf-8-sig")
    return df_sheet

def generate_installments(label, category, date_str, total_amount, installments, payment_method_name, ignore):
    
    payment_method_info = get_payment_methods(payment_method_name)

    statement_close_day = payment_method_info["statement_close_day"]
    payment_day = payment_method_info["payment_day"]
    method_type = payment_method_info["type"]

    ignore = 1 if ignore == 1 else 0

    base_date = datetime.strptime(date_str, "%Y-%m-%d")

    if method_type == "Credit":

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
            "Ignore Entry": ignore
        })
    return records

def fill_months(df, date_column, value_column):
    if not df.empty:
        all_months = pd.date_range(df[date_column].min(), df[date_column].max(), freq='MS')
        df = df.set_index(date_column).reindex(all_months, fill_value=0).rename_axis(date_column).reset_index()
        df[date_column] = pd.to_datetime(df[date_column], dayfirst=False)
        df.columns = [date_column, value_column]
    return df

def generate_pastel_colors_auto(df, column):
    n = df[column].nunique()
    cmap = matplotlib.colormaps.get_cmap("Pastel1").resampled(n)
    return [mcolors.rgb2hex(cmap(i)) for i in range(n)]

def empty_figure(message="No data available"):
    fig = go.Figure()
    fig.add_annotation(
        text=message,
        xref="paper", yref="paper",
        x=0.5, y=0.5,
        showarrow=False,
        font=dict(size=16, color=config.blue_1)
    )
    fig.update_layout(
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        plot_bgcolor=config.blue_2
    )
    return fig

# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# Dashboard app
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

current_year = datetime.now().year
default_start_date = f"{current_year}-01-01"
default_end_date = f"{current_year}-12-31"

app = Dash(
    __name__,
    external_stylesheets=[dbc.themes.BOOTSTRAP, dbc.icons.FONT_AWESOME],
    title="Financial Control"
)

if config.request_password:
    auth = dash_auth.BasicAuth(app, config.valid_users)

app.layout = html.Div([

    # Title
    html.H1(
        "Financial Control",
        className="text-center my-4 py-2",
        style={"backgroundColor": config.blue_3, "color": "white"}
    ),

    html.P(dbc.Container([

        dcc.Store(id="update-trigger", data=0),

        # IA Comment

        dbc.Row([
            dbc.Col(
                dbc.Card(
                    dbc.CardBody([
                        dbc.Row([
                            dbc.Col(html.Div("AI Insight (last 12 months)", style={"fontWeight": "bold"}), width="auto"),
                            dbc.Col(html.Div(id="ai-status", style={"textAlign": "right"})),
                        ]),
                        html.Hr(),
                        dcc.Markdown(
                            id="ai-comment",
                            children="Loading...",
                            style={"whiteSpace": "pre-wrap"}
                        ),
                    ]),
                    style={
                        "backgroundColor": config.blue_2,
                        "borderColor": config.blue_2,
                        "color": config.blue_1,
                        "fontSize": config.fontsize_1
                    }
                ),
                width=12
            )
        ], className="mb-4"),

        # html.Hr(style={"borderColor": "#35a79c", "borderWidth": "3px", "margin": "10px 0"}),

        # Filters
        dbc.Row([
            dbc.Col([
                dbc.Collapse(
                    dbc.Card(
                        dbc.CardBody([
                            dbc.Row([

                                dbc.Col([
                                    dbc.Label("Start Payment Date"),
                                    dbc.Input(
                                        id="date-start",
                                        type="date",
                                        value=default_start_date,
                                        style={"width": "100%"}
                                    ),
                                ], width=3, className="d-flex flex-column align-items-center justify-content-center"),

                                dbc.Col([
                                    dbc.Label("End Payment Date"),
                                    dbc.Input(
                                        id="date-end",
                                        type="date",
                                        value=default_end_date,
                                        style={"width": "100%"}
                                    ),
                                ], width=3, className="d-flex flex-column align-items-center justify-content-center")

                            ])
                        ], style={
                            "backgroundColor": config.blue_2,
                            "borderColor": config.blue_2,
                            "color": config.blue_1,
                            "fontSize": config.fontsize_3
                            }), style={
                                "backgroundColor": config.blue_2,
                                "borderColor": config.blue_2,
                                "color": config.blue_1,
                                "fontSize": config.fontsize_1
                                }
                    ),
                    id="filters-collapse",
                    is_open=False,
                    style={"width": "100%"}
                )
            ], width=24, className="ms-auto")
        ], className="my-4"),

        # Buttons
        dbc.Row([
            dbc.Col(dbc.Button(html.I(className="fa-solid fa-filter"), id="toggle-filters",
                               className="me-2", style={"backgroundColor": config.blue_2,
                                                        "borderColor": config.blue_2,
                                                        "color": config.blue_1,
                                                        "fontSize": config.fontsize_1}), width="auto"),
            dbc.Col(dbc.Button(html.I(className="fa-solid fa-arrow-rotate-left"), id="reset-btn",
                               className="me-2", style={"backgroundColor": config.blue_2,
                                                        "borderColor": config.blue_2,
                                                        "color": config.blue_1,
                                                        "fontSize": config.fontsize_1}), width="auto"),
            dbc.Col(dbc.Button(html.I(className="fa fa-credit-card"), id="open-payment-methods-modal",
                               className="me-2", style={"backgroundColor": config.blue_2,
                                                        "borderColor": config.blue_2,
                                                        "color": config.blue_1,
                                                        "fontSize": config.fontsize_1}), width="auto"),
            dbc.Col(dbc.Button(html.I(className="fa fa-plus"), id="open-modal",
                               style={"backgroundColor": config.blue_2,
                                      "borderColor": config.blue_2,
                                      "color": config.blue_1,
                                      "fontSize": config.fontsize_1}), width="auto"),
            dbc.Col(dbc.Button("Generate AI Insight", id="update-ai-comment-btn",
                               className="me-2", style={"backgroundColor": config.blue_2,
                                                        "borderColor": config.blue_2,
                                                        "color": config.blue_1,
                                                        "fontSize": config.fontsize_1}), width="auto"),
        ], className="my-4"),

        # New Record Modal
        dbc.Modal([
            dbc.ModalHeader("New Record",
                            style={"backgroundColor": config.gray_1, "fontWeight": "bold", "fontSize": config.fontsize_2}),
            dbc.ModalBody([

                dbc.Row([
                    
                    dbc.Col([
                        dbc.Label("Transaction Date"), dbc.Input(id="input-date", type="date"),
                    ], width=6, className="pe-2"),  # pe-2 = padding right

                    dbc.Col([
                        dbc.Label("Amount"), dbc.Input(id="input-amount", type="number", step="0.01"),
                    ], width=6, className="ps-2"),  # ps-2 = padding left 

                ], className="mb-3"),

                dbc.Label("Label"), dbc.Input(id="input-label", type="text", className="mb-3"),
                dbc.Label("Category"), dbc.Select(id="input-category", options=[{"label": m} for m in get_categories()], className="mb-3"),
                
                dbc.Row([
                    
                    dbc.Col([
                        dbc.Label("Payment Method"),
                        dbc.Select(
                            id="input-payment-method",
                            options=[{"label": m, "value": m} for m in get_payment_methods().keys()],
                            value=list(get_payment_methods().keys())[0] if get_payment_methods() else None
                        )
                    ], width=6, className="pe-2"),  # ps-2 = padding left

                    dbc.Col([
                        dbc.Label("Installments"),
                        dbc.Input(id="input-installments", type="number", min=1, value=1)
                    ], width=6, className="ps-2"),  # pe-2 = padding right

                ], className="mb-4"),

                dbc.Checkbox(id="input-ignore", label="Ignore entry?", value=False, className="mb-3"),
            ], style={"backgroundColor": config.gray_1, "fontWeight": "bold", "fontSize": config.fontsize_1}),
            dbc.ModalFooter([
                dbc.Button("Insert", id="btn-save",
                           style={"backgroundColor": config.blue_4, "borderColor": config.blue_4, "color": "white", "fontSize": config.fontsize_1}),
                dbc.Button("Cancel", id="btn-close",
                           style={"backgroundColor": config.gray_2, "borderColor": config.gray_2, "color": "white", "fontSize": config.fontsize_1}),
            ], style={"backgroundColor": config.gray_1}),
        ], id="modal", is_open=False),

        # Payment Methods Modal
        dbc.Modal([
            dbc.ModalHeader("Manage Payment Methods",
                            style={"backgroundColor": config.gray_1, "fontWeight": "bold", "fontSize": config.fontsize_2}),
            dbc.ModalBody([
                dash_table.DataTable(
                    id="table-payment-methods",
                    columns=[{"name": c, "id": c, "editable": True} for c in ["Name", "Close Date", "Payment Date", "Type"]],
                    data=load_payment_methods().to_dict("records"),
                    editable=True, row_deletable=True,
                    style_table={"overflowX": "auto"},
                    style_header={"backgroundColor": config.blue_2, "color": "white", "fontWeight": "bold"},
                    style_cell={"backgroundColor": "white", "color": "black", "textAlign": "center"}
                ),
                dbc.Button("Add", id="btn-add-payment-method",
                           className="mt-2 float-end",
                           style={"backgroundColor": config.blue_2, "borderColor": config.blue_2, "color": "white", "fontSize": config.fontsize_1})
            ], style={"backgroundColor": config.gray_1, "padding": "30px"}),
            dbc.ModalFooter([
                dbc.Button("Save", id="btn-save-payment-methods",
                           style={"backgroundColor": config.blue_4, "borderColor": config.blue_4, "color": "white", "fontSize": config.fontsize_1}),
                dbc.Button("Close", id="btn-close-payment-methods",
                           style={"backgroundColor": config.gray_2, "borderColor": config.gray_2, "color": "white", "fontSize": config.fontsize_1}),
            ], style={"backgroundColor": config.gray_1})
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
            dbc.Col(dbc.Button('Delete entry', id="btn-delete-hash-selected",
                               style={"backgroundColor": config.blue_2,
                                      "borderColor": config.blue_2,
                                      "color": config.blue_1,
                                      "fontSize": config.fontsize_1}), width="auto"),
            dbc.Col(dbc.Button('Delete selected installments', id="btn-delete-selected",
                               style={"backgroundColor": config.blue_2,
                                      "borderColor": config.blue_2,
                                      "color": config.blue_1,
                                      "fontSize": config.fontsize_1}), width="auto")
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
                    style_header={"backgroundColor": config.blue_2, "color": config.blue_1, "fontSize": config.fontsize_1},
                    style_cell={"backgroundColor": config.blue_3, "color": config.blue_1,
                                "textAlign": "center", "minWidth": "100px", "whiteSpace": "normal",
                                "fontSize": config.fontsize_1},
                    row_selectable="multi",
                    selected_rows=[],
                    filter_action="none",
                    sort_action="none",
                    page_action="none",
                    editable=False
                ),
                width=12
            )
        ], className="mb-4"),

    ], fluid=True, style={"backgroundColor": config.blue_3}))

], style={"backgroundColor": config.blue_3, "minHeight": "100vh", "padding": "20px"})

# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# Callbacks
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

# Callback to show/hide filters

@app.callback(
    Output("filters-collapse", "is_open"),
    Input("toggle-filters", "n_clicks"),
    prevent_initial_call=True
)
def toggle_filters(n_clicks):
    return n_clicks % 2 == 1

# Callback to reset filters

@app.callback(
    Output("date-start", "value"),
    Output("date-end", "value"),
    Input("reset-btn", "n_clicks"),
    prevent_initial_call=True
)
def reset_filters(n_clicks):
    return default_start_date, default_end_date

# Toggle Payment Methods modal

@app.callback(
    [Output("modal-payment-methods", "is_open"),
     Output("input-payment-method", "options")],
    [Input("open-payment-methods-modal", "n_clicks"),
     Input("btn-close-payment-methods", "n_clicks"),
     Input("btn-save-payment-methods", "n_clicks")],
    [State("modal-payment-methods", "is_open"), State("table-payment-methods", "data")]
)
def toggle_payment_methods_modal(open_click, close_click, save_click, is_open, table_data):
    trigger = callback_context.triggered_id
    if trigger == "btn-save-payment-methods" and table_data:
        df_payment_methods = pd.DataFrame(table_data)
        save_payment_methods(df_payment_methods)

    if open_click or close_click or save_click:
        return not is_open, [{"label": m, "value": m} for m in get_payment_methods().keys()]
    return is_open, [{"label": m, "value": m} for m in get_payment_methods().keys()]

@app.callback(
    Output("table-payment-methods", "data"),
    [Input("btn-add-payment-method", "n_clicks")],
    [State("table-payment-methods", "data"), State("table-payment-methods", "columns")],
    prevent_initial_call=True
)
def add_payment_method(n_clicks, rows, columns):
    rows.append({c["id"]: "" for c in columns})
    return rows

# Toggle modal

@app.callback(
    Output("modal", "is_open"),
    [Input("open-modal", "n_clicks"), Input("btn-close", "n_clicks")],
    [State("modal", "is_open")],
)
def toggle_modal(open_click, close_click, is_open):
    if open_click or close_click:
        return not is_open
    return is_open

# Single callback to save, delete selected rows, and update charts/table

@app.callback(
    [Output("fig1", "figure"), Output("fig2", "figure"), Output("fig3", "figure"), Output("fig4", "figure"), Output("fig5", "figure"), Output("fig6", "figure"), Output("fig7", "figure"),
     Output("table-data", "data"), Output("table-data", "columns"),
     Output("input-label", "value"), Output("input-category", "value"),
     Output("input-date", "value"), Output("input-amount", "value"), Output("input-installments", "value"), Output("input-ignore", "value"),
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
    prevent_initial_call=False
)

def update_all(save_click, delete_click_hash, delete_click, update_trigger, start_date, end_date, label,
               category, date, amount, installments, payment_method, ignore, selected_rows, table_data):
    
    trigger = callback_context.triggered_id
    
    df = load_data()

    # Save new record

    if trigger == "btn-save" and all([label, category, date, amount, installments, payment_method]):

        try:
            records = generate_installments(label, category, date, float(amount), int(installments), payment_method, ignore)
            
            df = pd.concat([df, pd.DataFrame(records)], ignore_index=True)
            df.to_csv(CSV_PATH, sep=";", index=False, encoding="utf-8-sig")
            df = load_data()
        
        except ValueError:
            pass
        clear_label, clear_category, clear_date, clear_amount, clear_ignore, clear_installments = "", None, None, None, None, False
    else:
        clear_label, clear_category, clear_date, clear_amount, clear_ignore, clear_installments = None, None, None, None, None, False

    # Delete selected rows

    if trigger == "btn-delete-hash-selected" and selected_rows:
        
        df_table = pd.DataFrame(table_data)
        hashes_to_delete = df_table.iloc[selected_rows]["Hash"].tolist()
        df = df[~df["Hash"].isin(hashes_to_delete)]
        df.to_csv(CSV_PATH, sep=";", index=False, encoding="utf-8-sig")

    if trigger == "btn-delete-selected" and selected_rows:

        df_table = pd.DataFrame(table_data)
        rows_to_delete = df_table.iloc[selected_rows][["Hash", "Payment Date"]]
        rows_to_delete["Payment Date"] = pd.to_datetime(rows_to_delete["Payment Date"], format="%d/%m/%Y")
        df = df[~df.set_index(["Hash", "Payment Date"]).index.isin(rows_to_delete.set_index(["Hash", "Payment Date"]).index)]
        df.to_csv(CSV_PATH, sep=";", index=False, encoding="utf-8-sig")

    # Update table
    
    columns_to_show = ["Record Timestamp", "Transaction Date", "Payment Date", "Label", "Category", "Amount", "Payment Method", "Hash"]
    
    df_table = df.copy()

    # Convert to datetime
    df_table["Record Timestamp"] = pd.to_datetime(df_table["Record Timestamp"], errors="coerce", format="%Y-%m-%d %H:%M:%S")
    df_table["Transaction Date"] = pd.to_datetime(df_table["Transaction Date"], errors="coerce", format="%Y-%m-%d")
    df_table["Payment Date"] = pd.to_datetime(df_table["Payment Date"], errors="coerce", format="%Y-%m-%d")

    # Format dates as strings in DD/MM/YYYY format
    df_table["Record Timestamp"] = df_table["Record Timestamp"].dt.strftime("%d/%m/%Y %H:%M:%S")
    df_table["Transaction Date"] = df_table["Transaction Date"].dt.strftime("%d/%m/%Y")
    df_table["Payment Date"] = df_table["Payment Date"].dt.strftime("%d/%m/%Y")

    # Sort and select columns
    df_table = df_table.sort_values("Record Timestamp", ascending=False).head(40)
    df_table = df_table[columns_to_show]

    # Prepare DataTable data
    columns = [{"name": col, "id": col} for col in df_table.columns]
    data = df_table.to_dict("records")

    # Filter by selected date range

    df_filtered_global = df.copy()

    df_filtered_global["Payment Date"] = pd.to_datetime(df_filtered_global["Payment Date"], errors="coerce", format="%Y-%m-%d")

    if start_date:
        df_filtered_global = df_filtered_global[df_filtered_global["Payment Date"] >= pd.to_datetime(start_date)]
    if end_date:
        df_filtered_global = df_filtered_global[df_filtered_global["Payment Date"] <= pd.to_datetime(end_date)]
    
    if not df_filtered_global.empty:
        
        # Chart 1

        df_g1 = df_filtered_global.copy()

        df_g1 = df_g1[df_g1["Ignore Entry"] == 0]
        df_g1["Payment Date"] = pd.to_datetime(df_g1["Payment Date"], format="%Y-%m-%d")
        df_g1 = df_g1.groupby(df_g1["Payment Date"].dt.to_period("M"))["Amount"].sum().reset_index()
        df_g1["Payment Date"] = df_g1["Payment Date"].dt.to_timestamp()
        df_g1 = fill_months(df_g1, "Payment Date", "Amount")
        df_g1["MonthYear"] = df_g1["Payment Date"].dt.strftime("%b %Y")
        df_g1["Cumulative_Amount"] = df_g1["Amount"].cumsum()

        if not df_g1.empty:
            
            fig1 = px.bar(df_g1, x="MonthYear", y="Cumulative_Amount", title="Cumulative amount",
                        color_discrete_sequence=[config.blue_1])
            fig1.update_layout(plot_bgcolor=config.blue_2, paper_bgcolor=config.blue_2,
                            title_font_color=config.blue_1, font_color=config.blue_1, font_size=config.chart_fontsize_1,
                            xaxis_title="Month/Year", yaxis_title="Amount",
                            xaxis=dict(
                                showgrid=False,
                                gridcolor=config.gray_1,
                                gridwidth=1.0
                            ),
                            yaxis=dict(
                                gridcolor=config.gray_1,
                                gridwidth=1.0,
                                zerolinecolor=config.gray_3,
                                zerolinewidth=3.0
                            ))
        else:
            fig1 = empty_figure()

        # Chart 2

        df_g2 = df_filtered_global.copy()

        df_g2 = df_g2[df_g2["Ignore Entry"] == 0]
        df_g2 = df_g2[df_g2["Amount"] < 0]
        df_g2["Amount"] = df_g2["Amount"].abs()

        predefined_color_palette = generate_pastel_colors_auto(df_g2, "Payment Method")

        if not df_g2.empty:

            fig2 = px.pie(df_g2.groupby("Payment Method")["Amount"].sum().reset_index(), names="Payment Method", values="Amount",
                        title="Spending by Payment Method", color_discrete_sequence=predefined_color_palette)
            fig2.update_layout(plot_bgcolor=config.blue_2, paper_bgcolor=config.blue_2,
                            title_font_color=config.blue_1, font_color=config.blue_1, font_size=config.chart_fontsize_1)
        else:
            fig2 = empty_figure()

        # Chart 3

        df_g3 = df_filtered_global.copy()

        df_g3 = df_g3[df_g3["Ignore Entry"] == 0]
        df_g3 = df_g3[df_g3["Amount"] < 0]
        df_g3["Amount"] = df_g3["Amount"].abs()

        predefined_color_palette = generate_pastel_colors_auto(df_g3, "Category")

        if not df_g3.empty:

            fig3 = px.pie(df_g3.groupby("Category")["Amount"].sum().reset_index(), names="Category", values="Amount",
                        title="Spending by Category", color_discrete_sequence=predefined_color_palette)
            fig3.update_layout(plot_bgcolor=config.blue_2, paper_bgcolor=config.blue_2,
                            title_font_color=config.blue_1, font_color=config.blue_1, font_size=config.chart_fontsize_1)
        else:
            fig3 = empty_figure()

        # Chart 4

        df_g4 = df_filtered_global.copy()
        
        df_g4 = df_g4[df_g4["Amount"] < 0]
        df_g4["Amount"] = df_g4["Amount"].abs()
        df_g4["Payment Date"] = pd.to_datetime(df_g4["Payment Date"], format="%Y-%m-%d")
        df_g4 = df_g4.groupby(df_g4["Payment Date"].dt.to_period("M"))["Amount"].sum().reset_index()
        df_g4["Payment Date"] = df_g4["Payment Date"].dt.to_timestamp()
        df_g4 = fill_months(df_g4, "Payment Date", "Amount")
        df_g4["MonthYear"] = df_g4["Payment Date"].dt.strftime("%b %Y")
        
        if not df_g4.empty:

            fig4 = px.bar(df_g4, x="MonthYear", y="Amount", title="Amount paid per month",
                        color_discrete_sequence=[config.red_1])
            fig4.update_layout(plot_bgcolor=config.blue_2, paper_bgcolor=config.blue_2,
                            title_font_color=config.blue_1, font_color=config.blue_1, font_size=config.chart_fontsize_1,
                            xaxis_title="Month/Year", yaxis_title="Amount",
                            xaxis=dict(
                                showgrid=False,
                                gridcolor=config.gray_1,
                                gridwidth=1.0
                            ),
                            yaxis=dict(
                                gridcolor=config.gray_1,
                                gridwidth=1.0,
                                zerolinecolor=config.gray_3,
                                zerolinewidth=3.0
                            ))
        else:
            fig4 = empty_figure()

        # Chart 5

        df_g5 = df_filtered_global.copy()

        df_g5 = df_g5[df_g5["Ignore Entry"] == 0]
        df_g5 = df_g5[df_g5["Amount"] < 0]
        df_g5["Amount"] = df_g5["Amount"].abs()
        df_g5["Transaction Date"] = pd.to_datetime(df_g5["Transaction Date"], format="%Y-%m-%d")
        df_g5 = df_g5.groupby(df_g5["Transaction Date"].dt.to_period("M"))["Amount"].sum().reset_index()
        df_g5["Transaction Date"] = df_g5["Transaction Date"].dt.to_timestamp()
        df_g5 = fill_months(df_g5, "Transaction Date", "Amount")
        df_g5["MonthYear"] = df_g5["Transaction Date"].dt.strftime("%b %Y")
        
        if not df_g5.empty:

            fig5 = px.bar(df_g5, x="MonthYear", y="Amount", title="Amount spent per month",
                        color_discrete_sequence=[config.red_1])
            fig5.update_layout(plot_bgcolor=config.blue_2, paper_bgcolor=config.blue_2,
                            title_font_color=config.blue_1, font_color=config.blue_1, font_size=config.chart_fontsize_1,
                            xaxis_title="Month/Year", yaxis_title="Amount",
                            xaxis=dict(
                                showgrid=False,
                                gridcolor=config.gray_1,
                                gridwidth=1.0
                            ),
                            yaxis=dict(
                                gridcolor=config.gray_1,
                                gridwidth=1.0,
                                zerolinecolor=config.gray_3,
                                zerolinewidth=3.0
                            ))
        else:
            fig5 = empty_figure()

        # Chart 6
        
        df_g6 = df_filtered_global.copy()

        df_g6 = df_g6[df_g6["Ignore Entry"] != 1.0]
        df_g6 = df_g6[df_g6["Amount"] < 0]
        df_g6["Amount"] = df_g6["Amount"].abs()
        df_g6["Payment Date"] = pd.to_datetime(df_g6["Payment Date"], format="%Y-%m-%d")
        df_g6["Installment"] = df_g6["Installment"].astype(str)
        df_g6 = df_g6[df_g6["Installment"].str.contains("/")]
        df_g6 = df_g6[df_g6["Installment"].str.split("/").apply(lambda x: int(x[1]) > 1)]
        df_g6 = df_g6[df_g6.apply(lambda row: row["Installment"].endswith(f"{int(row['Installment'].split('/')[1])}/{int(row['Installment'].split('/')[1])}"), axis=1)]
        df_g6 = df_g6.groupby(df_g6["Payment Date"].dt.to_period("M"))["Amount"].sum().reset_index()
        df_g6["Payment Date"] = df_g6["Payment Date"].dt.to_timestamp()
        df_g6 = fill_months(df_g6, "Payment Date", "Amount")
        df_g6["MonthYear"] = df_g6["Payment Date"].dt.strftime("%b %Y")
        
        if not df_g6.empty:

            fig6 = px.bar(df_g6, x="MonthYear", y="Amount", title="Finishing payments", color_discrete_sequence=[config.green_1])
            fig6.update_layout(plot_bgcolor=config.blue_2, paper_bgcolor=config.blue_2,
                            title_font_color=config.blue_1, font_color=config.blue_1, font_size=config.chart_fontsize_1,
                            xaxis_title="Month/Year", yaxis_title="Amount",
                            xaxis=dict(
                                showgrid=False,
                                gridcolor=config.gray_1,
                                gridwidth=1.0
                            ),
                            yaxis=dict(
                                gridcolor=config.gray_1,
                                gridwidth=1.0,
                                zerolinecolor=config.gray_3,
                                zerolinewidth=3.0
                            ))
        else:
            fig6 = empty_figure()

        # Chart 7
        
        df_g7 = df_filtered_global.copy()

        df_g7 = df_g7[df_g7["Ignore Entry"] == 0]
        df_g7 = df_g7[df_g7["Amount"] < 0]
        df_g7["Amount"] = df_g7["Amount"].abs()
        df_g7["Payment Date"] = pd.to_datetime(df_g7["Payment Date"], format="%Y-%m-%d")
        df_g7["Installment"] = df_g7["Installment"].astype(str)
        df_g7 = df_g7[df_g7["Installment"].str.contains("/")]
        df_g7 = df_g7[df_g7["Installment"].str.split("/").apply(lambda x: int(x[1]) > 1)]
        df_g7 = df_g7[df_g7.apply(lambda row: row["Installment"] == f"1/{int(row['Installment'].split('/')[1])}", axis=1)]
        df_g7 = df_g7.groupby(df_g7["Payment Date"].dt.to_period("M"))["Amount"].sum().reset_index()
        df_g7["Payment Date"] = df_g7["Payment Date"].dt.to_timestamp()
        df_g7 = fill_months(df_g7, "Payment Date", "Amount")
        df_g7["MonthYear"] = df_g7["Payment Date"].dt.strftime("%b %Y")
        
        if not df_g7.empty:

            fig7 = px.bar(df_g7, x="MonthYear", y="Amount", title="Starting payments", color_discrete_sequence=[config.yellow_1])
            fig7.update_layout(plot_bgcolor=config.blue_2, paper_bgcolor=config.blue_2,
                            title_font_color=config.blue_1, font_color=config.blue_1, font_size=config.chart_fontsize_1,
                            xaxis_title="Month/Year", yaxis_title="Amount",
                            xaxis=dict(
                                showgrid=False,
                                gridcolor=config.gray_1,
                                gridwidth=1.0
                            ),
                            yaxis=dict(
                                showgrid=True,
                                gridcolor=config.gray_1,
                                gridwidth=1.0,
                                zerolinecolor=config.gray_3,
                                zerolinewidth=3.0
                            ))
        else:
            fig7 = empty_figure()
    else:

        fig1 = empty_figure()
        fig2 = empty_figure()
        fig3 = empty_figure()
        fig4 = empty_figure()
        fig5 = empty_figure()
        fig6 = empty_figure()
        fig7 = empty_figure()
        
    return fig1, fig2, fig3, fig4, fig5, fig6, fig7, data, columns, clear_label, clear_category, clear_date, clear_amount, clear_installments, clear_ignore, []

# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# Callbacks for IA Interaction
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

@app.callback(
    Output("ai-comment", "children"),
    Output("ai-status", "children"),
    Input("update-ai-comment-btn", "n_clicks"),     # single trigger after opening
    State("date-start", "value"),                   # it doesn't fire
    State("date-end", "value"),                     # it doesn't fire
    prevent_initial_call=False                      # executes when opened
)

def update_ai_comment(update_trigger, start_date, end_date):
    
    df = load_data()

    df["Payment Date"] = pd.to_datetime(df["Payment Date"], errors="coerce", format="%Y-%m-%d")
    df = df.dropna(subset=["Payment Date"])

    if start_date:
        df = df[df["Payment Date"] >= pd.to_datetime(start_date)]
    if end_date:
        df = df[df["Payment Date"] <= pd.to_datetime(end_date)]

    metrics = compute_last12m_metrics(df, end_date=end_date)
    status, text = get_ai_comment(metrics, model=config.ollama_model)
    
    text += f'\n\nLast update: {get_datetime(config.timezone).strftime('%Y-%m-%d %H:%M:%S')}'

    return text, f"Status: {status}"

if __name__ == "__main__":
    threading.Thread(target=open_loading_page, daemon=True).start()        # Open loading page in a thread so it doesn't block the server
    time.sleep(1)                                                          # Wait 1 or 2 seconds to ensure the loading tab opened
    app.run(debug=True, host="0.0.0.0", port=8050, use_reloader=False)     # Start Dash normally
