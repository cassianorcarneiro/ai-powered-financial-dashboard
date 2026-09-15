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


def compute_window_metrics(
    df: pd.DataFrame,
    end_date: str | None,
    payment_method_types: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Aggregate the trailing 12 months ending at `end_date` (or at the last record).

    The returned dictionary is the only thing sent to the language model, so it
    deliberately contains aggregates rather than individual transactions.

    `payment_method_types` maps a payment method's name to "Credit" or "Debit"
    (`storage.get_payment_methods()`, read at the call site). This module stays
    decoupled from storage.py; passing the mapping in keeps that boundary while
    still letting the credit/debit split be computed here alongside everything
    else. Omit it and that split is simply left out of the result.
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

    income_last = _avg_tail(monthly_income, TREND_MONTHS)
    income_prev = _avg_tail(monthly_income.iloc[:-TREND_MONTHS], TREND_MONTHS)
    income_trend = (
        (income_last - income_prev) / income_prev if income_prev > 0 else None
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

    # Share of spending by payment method type (Credit vs. Debit), when the
    # caller supplied the mapping. A method with no match (deleted, renamed
    # since the transaction was recorded) falls into "Unknown" rather than
    # being silently dropped, so the shares are still traceable back to 100%
    # of the window's expense total.
    payment_type_share: dict[str, float] | None = None
    if payment_method_types:
        pm_type = expenses["Payment Method"].map(payment_method_types).fillna("Unknown")
        by_type = expenses.groupby(pm_type)["abs_amount"].sum()
        if expense > 0:
            payment_type_share = {
                str(k): round(float(v) / expense, 4) for k, v in by_type.items()
            }

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

    # Forward-looking, unlike everything above: uses `d` (every expense on
    # record) rather than `window`, because a purchase's remaining or upcoming
    # installments legitimately fall after `end_ts`, outside the trailing
    # 12-month lookback the rest of this function reports on. Scoped to
    # multi-installment purchases only — a single future-dated expense is not
    # a "commitment" in the sense meant here, just an ordinary entry that
    # hasn't come due yet.
    HORIZON_MONTHS = 3
    horizon_end = end_ts + relativedelta(months=HORIZON_MONTHS)

    future_expenses = d[(d["Amount"] < 0) & (d["Ignore Entry"] == 0)].copy()
    future_expenses["abs_amount"] = future_expenses["Amount"].abs()
    future_expenses["parsed"] = future_expenses["Installment"].map(parse_installment)
    future_installments = future_expenses[future_expenses["parsed"].notna()].copy()
    future_installments["index"] = future_installments["parsed"].map(lambda p: p[0])
    future_installments["count"] = future_installments["parsed"].map(lambda p: p[1])

    not_yet_paid = future_installments[future_installments["Payment Date"] > end_ts]
    remaining_committed_value = float(not_yet_paid["abs_amount"].sum())

    last_installment_rows = future_installments[
        future_installments["index"] == future_installments["count"]
    ]
    finishing_soon = last_installment_rows[
        (last_installment_rows["Payment Date"] > end_ts)
        & (last_installment_rows["Payment Date"] <= horizon_end)
    ]

    first_installment_rows = future_installments[future_installments["index"] == 1]
    starting_soon = first_installment_rows[
        (first_installment_rows["Payment Date"] > end_ts)
        & (first_installment_rows["Payment Date"] <= horizon_end)
    ]

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
            "income_trend_3m": _round_or_none(income_trend),
        },
        "top": {
            "categories": _round_map(top_categories),
            "payment_methods": _round_map(top_methods),
        },
        "payment_type_share": payment_type_share,
        "largest_expense": largest_expense,
        "installment_share": _round_or_none(installment_share),
        "upcoming_installments": {
            "horizon_months": HORIZON_MONTHS,
            "remaining_committed_value": round(remaining_committed_value, 2),
            "finishing_soon": {
                "count": int(len(finishing_soon)),
                "value": round(float(finishing_soon["abs_amount"].sum()), 2),
            },
            "starting_soon": {
                "count": int(len(starting_soon)),
                "value": round(float(starting_soon["abs_amount"].sum()), 2),
            },
        },
        "months_count": int(len(monthly_expense)),
    }
