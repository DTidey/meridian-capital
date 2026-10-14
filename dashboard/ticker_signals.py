"""Ticker page: trading signal, factor sub-scores, history charts and insider clusters."""

from __future__ import annotations

import html
import math

import pandas as pd
import plotly.graph_objects as go
import sqlalchemy as sa
import streamlit as st

from analysis.db import combined_scores
from dashboard.theme import ACCENT, LONG_COL, NEUTRAL, SHORT_COL, metric_card, section_header
from dashboard.ticker_common import chart_layout, score_colour
from data.db import analyst_estimates, insider_cluster_flags, short_interest
from factors.db import factor_scores

# (factor, factor_scores column, label) for every sub-factor column
SUBFACTORS = [
    ("Momentum", "mom_12_1", "12-1 month return"),
    ("Momentum", "mom_6m", "6 month return"),
    ("Momentum", "mom_3m", "3 month return"),
    ("Momentum", "mom_accel", "Acceleration"),
    ("Momentum", "mom_52w_high", "Near 52-week high"),
    ("Momentum", "mom_rel_strength", "Relative strength"),
    ("Value", "val_fwd_earn_yield", "Forward earnings yield"),
    ("Value", "val_book_to_price", "Book to price"),
    ("Value", "val_fcf_yield", "FCF yield"),
    ("Value", "val_ev_ebitda_inv", "EBITDA / EV"),
    ("Value", "val_shareholder_yield", "Shareholder yield"),
    ("Value", "val_sales_to_ev", "Sales / EV"),
    ("Quality", "qual_roe_stability", "ROE stability"),
    ("Quality", "qual_gm_level", "Gross margin level"),
    ("Quality", "qual_gm_trend", "Gross margin trend"),
    ("Quality", "qual_de_inv", "Low debt / equity"),
    ("Quality", "qual_cfo_to_ni", "CFO / net income"),
    ("Quality", "qual_accruals_inv", "Low accruals"),
    ("Quality", "qual_piotroski", "Piotroski F-score"),
    ("Quality", "qual_altman_z", "Altman Z-score"),
    ("Growth", "grw_rev_yoy", "Revenue growth YoY"),
    ("Growth", "grw_earn_yoy", "Earnings growth YoY"),
    ("Growth", "grw_rev_accel", "Revenue acceleration"),
    ("Growth", "grw_rd_intensity", "R&D intensity"),
    ("Growth", "grw_fcf_yoy", "FCF growth YoY"),
    ("Revisions", "rev_30d", "30 day revisions"),
    ("Revisions", "rev_60d", "60 day revisions"),
    ("Revisions", "rev_90d", "90 day revisions"),
    ("Short Interest", "si_pct_float", "% float short"),
    ("Short Interest", "si_days_to_cover", "Days to cover"),
    ("Short Interest", "si_change", "Short interest change"),
    ("Insider", "ins_net_flow", "Net insider flow"),
    ("Insider", "ins_cluster_flag", "Cluster buying"),
    ("Institutional", "inst_funds_holding", "Funds holding"),
    ("Institutional", "inst_net_share_change", "Net share change"),
    ("Institutional", "inst_simultaneous_open", "Simultaneous opens"),
]


def _clean(value: float | None) -> float | None:
    return None if value is None or math.isnan(value) else value


def fmt_score(value: float | None) -> str:
    value = _clean(value)
    return "—" if value is None else f"{value:.0f}"


def direction_colour(direction: str | None) -> str:
    return {"LONG": LONG_COL, "SHORT": SHORT_COL}.get(direction or "", NEUTRAL)


def subfactor_rows(fs: dict) -> list[dict]:
    return [
        {"Factor": factor, "Sub-factor": label, "Score": _clean(fs.get(col))}
        for factor, col, label in SUBFACTORS
    ]


def render_signal(engine, ticker: str) -> None:
    section_header("SIGNAL")
    with engine.connect() as conn:
        row = conn.execute(
            sa.select(combined_scores)
            .where(combined_scores.c.ticker == ticker)
            .order_by(combined_scores.c.score_date.desc())
            .limit(1)
        ).fetchone()
        regime = conn.execute(
            sa.select(factor_scores.c.regime)
            .where(factor_scores.c.ticker == ticker)
            .order_by(factor_scores.c.score_date.desc())
            .limit(1)
        ).scalar()
    if row is None:
        st.caption("No combined score computed for this ticker.")
        return

    cs = row._asdict()
    st.caption(f"As of {cs['score_date']}")
    cols = st.columns(5)
    for col, label, key in zip(
        cols[:3],
        ("Combined", "Quant", "AI"),
        ("combined_score", "quant_composite", "ai_composite"),
        strict=True,
    ):
        with col:
            metric_card(label, fmt_score(cs[key]), score_colour(_clean(cs[key])))
    with cols[3]:
        direction = html.escape(cs["direction"] or "—")
        metric_card("Direction", direction, direction_colour(cs["direction"]))
    with cols[4]:
        metric_card("Regime", html.escape(regime or "—"))


def render_subfactors(fs: dict) -> None:
    with st.expander("Sub-factor scores", expanded=False):
        st.dataframe(
            pd.DataFrame(subfactor_rows(fs)),
            width="stretch",
            hide_index=True,
            column_config={"Score": st.column_config.NumberColumn(format="%.0f")},
        )


def render_estimate_trend(engine, ticker: str) -> None:
    with engine.connect() as conn:
        df = pd.read_sql(
            sa.select(
                analyst_estimates.c.date,
                analyst_estimates.c.price_target,
                analyst_estimates.c.eps_estimate_fwd,
            )
            .where(analyst_estimates.c.ticker == ticker)
            .order_by(analyst_estimates.c.date),
            conn,
        )
    _trend_chart(df, "price_target", "Price target ($)", "eps_estimate_fwd", "Fwd EPS ($)")


def render_short_interest_trend(engine, ticker: str) -> None:
    with engine.connect() as conn:
        df = pd.read_sql(
            sa.select(
                short_interest.c.date,
                short_interest.c.short_pct_float,
                short_interest.c.short_ratio,
            )
            .where(short_interest.c.ticker == ticker)
            .order_by(short_interest.c.date),
            conn,
        )
    df["short_pct_float"] = df["short_pct_float"] * 100
    _trend_chart(df, "short_pct_float", "% float short", "short_ratio", "Days to cover")


def _trend_chart(df: pd.DataFrame, left: str, left_name: str, right: str, right_name: str):
    """Two series on twin y-axes; skipped with fewer than two dated rows."""
    fig = trend_figure(df, left, left_name, right, right_name)
    if fig is not None:
        st.plotly_chart(fig, width="stretch")


def trend_figure(
    df: pd.DataFrame, left: str, left_name: str, right: str, right_name: str
) -> go.Figure | None:
    if df["date"].nunique() < 2:
        return None
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df["date"], y=df[left], name=left_name, line={"color": ACCENT}))
    fig.add_trace(
        go.Scatter(x=df["date"], y=df[right], name=right_name, yaxis="y2", line={"color": NEUTRAL})
    )
    fig.update_layout(
        **chart_layout(height=220),
        yaxis2={"overlaying": "y", "side": "right", "showgrid": False},
        legend_orientation="h",
    )
    return fig


def render_cluster_flags(engine, ticker: str) -> None:
    with engine.connect() as conn:
        flags = conn.execute(
            sa.select(insider_cluster_flags)
            .where(insider_cluster_flags.c.ticker == ticker)
            .order_by(insider_cluster_flags.c.window_start.desc())
        ).fetchall()
    for line in cluster_lines(flags):
        st.caption(line)


def cluster_lines(flags) -> list[str]:
    return [
        f"Insider cluster: {f.window_start} to {f.window_end}, "
        f"{f.insider_count} insiders, {f.total_shares or 0:,.0f} shares"
        for f in flags
    ]
