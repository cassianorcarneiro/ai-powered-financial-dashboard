# =============================================================================
# AI POWERED FINANCIAL DASHBOARD
# Aggregated metrics feeding the AI insight.
# =============================================================================

"""Trailing-window aggregations over the transactions table.

Sign convention throughout the project: expenses are stored as negative
amounts and income as positive amounts. Every function here assumes that and
converts to absolute values only for presentation.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from dateutil.relativedelta import relativedelta

from config import DATE_FORMAT

# Number of months in the analysis window and in each trend half.
WINDOW_MONTHS = 12
TREND_MONTHS = 3


def parse_installment(value: object) -> tuple[int, int] | None:
    """Parse an installment marker like '2/6' into (index, total).

    Returns None when the value is absent, malformed, or represents a
    single-payment transaction.
    """
    text = str(value).strip()
    if "/" not in text:
        return None
    index_str, _, total_str = text.partition("/")
    if not index_str.isdigit() or not total_str.isdigit():
        return None
    index, total = int(index_str), int(total_str)
    if total <= 1 or index < 1 or index > total:
        return None
    return index, total


def is_multi_installment(series: pd.Series) -> pd.Series:
    """Boolean mask selecting rows that belong to a multi-installment purchase."""
    return series.map(lambda value: parse_installment(value) is not None)


def _avg_tail(series: pd.Series, n: int) -> float:
    """Mean of the last `n` entries, or of everything available if shorter."""
    if series.empty:
        return 0.0
    if len(series) < n:
        return float(series.mean())
    return float(series.tail(n).mean())


def compute_window_metrics(df: pd.DataFrame, end_date: str | None) -> dict[str, Any]:
    """Aggregate the trailing 12 months ending at `end_date` (or at the last record).

    The returned dictionary is the only thing sent to the language model, so it
    deliberately contains aggregates rather than individual transactions.
    """
    if df.empty:
        return {"has_data": False}

    d = df.copy()
    d["Payment Date"] = pd.to_datetime(d["Payment Date"], errors="coerce", format=DATE_FORMAT)
    d = d.dropna(subset=["Payment Date"])
    if d.empty:
        return {"has_data": False}

    data_max = d["Payment Date"].max()
    end_ts = min(pd.to_datetime(end_date), data_max) if end_date else data_max
    start_ts = end_ts - relativedelta(months=WINDOW_MONTHS)

    window = d[(d["Payment Date"] > start_ts) & (d["Payment Date"] <= end_ts)].copy()
    window = window[window["Ignore Entry"] == 0]
    if window.empty:
        return {"has_data": False}

    income = float(window.loc[window["Amount"] > 0, "Amount"].sum())
    expense = float(window.loc[window["Amount"] < 0, "Amount"].abs().sum())

    # ----- Monthly series, gap-filled so trends are not distorted by empty months
    window["month"] = window["Payment Date"].dt.to_period("M").dt.to_timestamp()
    monthly_income = window[window["Amount"] > 0].groupby("month")["Amount"].sum()
    monthly_expense = window[window["Amount"] < 0].groupby("month")["Amount"].sum().abs()

    all_months = pd.date_range(
        start=window["month"].min(), end=window["month"].max(), freq="MS"
    )
    monthly_income = monthly_income.reindex(all_months, fill_value=0.0)
    monthly_expense = monthly_expense.reindex(all_months, fill_value=0.0)

    # ----- Trend: mean of the last 3 months against the 3 months before them
    expense_last = _avg_tail(monthly_expense, TREND_MONTHS)
    expense_prev = _avg_tail(monthly_expense.iloc[:-TREND_MONTHS], TREND_MONTHS)
    expense_trend = (
        (expense_last - expense_prev) / expense_prev if expense_prev > 0 else None
    )

    # ----- Dispersion: coefficient of variation of monthly expenses
    expense_mean = float(monthly_expense.mean())
    expense_std = float(monthly_expense.std(ddof=0))
    expense_cv = (expense_std / expense_mean) if expense_mean > 0 else None

    # ----- Rankings and outliers, expenses only
    expenses = window[window["Amount"] < 0].copy()
    expenses["abs_amount"] = expenses["Amount"].abs()

    top_categories = (
        expenses.groupby("Category")["abs_amount"].sum().sort_values(ascending=False).head(5)
    )
    top_methods = (
        expenses.groupby("Payment Method")["abs_amount"].sum().sort_values(ascending=False).head(5)
    )

    largest_expense = None
    if not expenses.empty:
        row = expenses.loc[expenses["abs_amount"].idxmax()]
        largest_expense = {
            "label": str(row.get("Label", "")),
            "category": str(row.get("Category", "")),
            "amount": round(float(row["abs_amount"]), 2),
            "payment_date": row["Payment Date"].strftime(DATE_FORMAT),
        }

    installment_mask = is_multi_installment(expenses["Installment"])
    installment_share = (
        float(expenses.loc[installment_mask, "abs_amount"].sum() / expense)
        if expense > 0
        else None
    )

    def _round_map(series: pd.Series) -> dict[str, float]:
        return {str(k): round(float(v), 2) for k, v in series.items()}

    def _round_or_none(value: float | None, digits: int = 4) -> float | None:
        return round(value, digits) if value is not None else None

    return {
        "has_data": True,
        "window": {
            "start": start_ts.strftime(DATE_FORMAT),
            "end": end_ts.strftime(DATE_FORMAT),
        },
        "totals": {
            "income": round(income, 2),
            "expense": round(expense, 2),
            "net": round(income - expense, 2),
        },
        "monthly": {
            "expense_mean": round(expense_mean, 2),
            "income_mean": round(float(monthly_income.mean()), 2),
            "expense_cv": _round_or_none(expense_cv),
            "expense_trend_3m": _round_or_none(expense_trend),
        },
        "top": {
            "categories": _round_map(top_categories),
            "payment_methods": _round_map(top_methods),
        },
        "largest_expense": largest_expense,
        "installment_share": _round_or_none(installment_share),
        "months_count": int(len(monthly_expense)),
    }
