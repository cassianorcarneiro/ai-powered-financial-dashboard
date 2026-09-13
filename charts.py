# =============================================================================
# AI POWERED FINANCIAL DASHBOARD
# Plotly figure factory.
# =============================================================================

"""Chart construction shared by every panel on the dashboard.

The pastel palette is taken from Plotly's own qualitative sets rather than from
Matplotlib colormaps. Matplotlib was the single heaviest dependency in the
image and was used only to generate a handful of hex colors.
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from config import DATE_FORMAT, Config as config

# Pastel palette, cycled when a chart has more slices than the palette has colors.
_PASTEL_PALETTE: list[str] = list(px.colors.qualitative.Pastel) + list(px.colors.qualitative.Set3)

# Sizing is split deliberately: the height is pinned, the width is not.
#
# `height` is an explicit pixel value because the dbc.Col around each graph is
# auto-sized. Left to derive its own height, Plotly could settle on a taller box
# than the row reserves and overlap the content below on a re-render.
#
# `autosize` stays on so the width is taken from the container. With autosize
# off and no explicit width, Plotly falls back to a default width instead of
# measuring the column, which leaves the chart the wrong size whenever the first
# paint happens before the layout has settled. autosize only governs dimensions
# the figure leaves undefined, so it adjusts the width and never touches the
# pinned height.
FIGURE_HEIGHT = 400

# Plotly's `separators` takes the decimal mark followed by the thousands mark.
# Passing only "." leaves the thousands group empty, so axis ticks and hover
# labels render as 1000.00 rather than 1,000.00. The grouping comma doubles as a
# decimal mark in most of the world, which makes it ambiguous on a dashboard of
# bare amounts; the table and the written summary omit it for the same reason.
DECIMAL_SEPARATORS = "."

# `fixedrange` locks each axis to its computed range. That is what actually
# disables zooming: with no zoomable range, Plotly ignores scroll wheel, drag
# selection, double-click autoscale and touch pinch alike, and drops the zoom
# controls from the modebar. Disabling the gestures one by one in the client
# config would leave gaps, since each input path is configured separately.
_AXIS_X = dict(
    showgrid=False,
    gridcolor=config.gray_1,
    gridwidth=1.0,
    fixedrange=True,
)
_AXIS_Y = dict(
    gridcolor=config.gray_1,
    gridwidth=1.0,
    zerolinecolor=config.gray_3,
    zerolinewidth=3.0,
    fixedrange=True,
)


def pastel_colors(n: int) -> list[str]:
    """Return `n` pastel colors, repeating the palette if necessary."""
    n = max(n, 1)
    palette = _PASTEL_PALETTE
    return [palette[i % len(palette)] for i in range(n)]


def empty_figure(message: str = "No data available") -> go.Figure:
    """Placeholder figure rendered when a panel has nothing to show."""
    fig = go.Figure()
    fig.add_annotation(
        text=message,
        xref="paper",
        yref="paper",
        x=0.5,
        y=0.5,
        showarrow=False,
        font=dict(size=16, color=config.blue_1),
    )
    fig.update_layout(
        height=FIGURE_HEIGHT,
        autosize=True,
        separators=DECIMAL_SEPARATORS,
        dragmode=False,
        xaxis=dict(visible=False, fixedrange=True),
        yaxis=dict(visible=False, fixedrange=True),
        plot_bgcolor=config.blue_2,
        paper_bgcolor=config.blue_2,
        margin=dict(t=40, b=40, l=40, r=40),
    )
    return fig


def _apply_common_layout(fig: go.Figure) -> go.Figure:
    """Apply the shared dark-panel styling."""
    fig.update_layout(
        height=FIGURE_HEIGHT,
        autosize=True,
        separators=DECIMAL_SEPARATORS,
        dragmode=False,
        plot_bgcolor=config.blue_2,
        paper_bgcolor=config.blue_2,
        title_font_color=config.blue_1,
        font_color=config.blue_1,
        font_size=config.chart_fontsize_1,
        margin=dict(t=60, b=50, l=50, r=30),
    )
    return fig


def _fill_month_gaps(df: pd.DataFrame, date_column: str) -> pd.DataFrame:
    """Reindex a monthly frame so every month in the range is present."""
    if df.empty:
        return df
    all_months = pd.date_range(df[date_column].min(), df[date_column].max(), freq="MS")
    filled = (
        df.set_index(date_column)
        .reindex(all_months, fill_value=0)
        .rename_axis(date_column)
        .reset_index()
    )
    return filled


def monthly_bar(
    df: pd.DataFrame,
    date_column: str,
    title: str,
    color: str,
    cumulative: bool = False,
) -> go.Figure:
    """Aggregate `df` by month on `date_column` and render it as a bar chart."""
    if df.empty:
        return empty_figure()

    d = df[[date_column, "Amount"]].copy()
    d[date_column] = pd.to_datetime(d[date_column], errors="coerce", format=DATE_FORMAT)
    d = d.dropna(subset=[date_column])
    if d.empty:
        return empty_figure()

    d = d.groupby(d[date_column].dt.to_period("M"))["Amount"].sum().reset_index()
    d[date_column] = d[date_column].dt.to_timestamp()
    d = _fill_month_gaps(d, date_column)
    if d.empty:
        return empty_figure()

    d["MonthYear"] = d[date_column].dt.strftime("%b %Y")
    y_column = "Amount"
    if cumulative:
        d["Cumulative"] = d["Amount"].cumsum()
        y_column = "Cumulative"

    fig = px.bar(
        d,
        x="MonthYear",
        y=y_column,
        title=title,
        color_discrete_sequence=[color],
    )
    fig.update_layout(
        xaxis_title="Month/Year",
        yaxis_title="Amount",
        xaxis=_AXIS_X,
        yaxis=_AXIS_Y,
    )
    return _apply_common_layout(fig)


def share_pie(df: pd.DataFrame, group_column: str, title: str) -> go.Figure:
    """Render the share of total expenses per value of `group_column`."""
    if df.empty or group_column not in df.columns:
        return empty_figure()

    grouped = df.groupby(group_column)["Amount"].sum().reset_index()
    grouped = grouped[grouped["Amount"] > 0]
    if grouped.empty:
        return empty_figure()

    fig = px.pie(
        grouped,
        names=group_column,
        values="Amount",
        title=title,
        color_discrete_sequence=pastel_colors(len(grouped)),
    )
    fig.update_traces(textposition="inside", textinfo="percent+label")
    return _apply_common_layout(fig)
