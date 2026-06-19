"""Page VII — Ticker: price chart, factor scores, fundamentals, and holdings for any universe ticker."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import sqlalchemy as sa
import streamlit as st

from analysis.db import ai_scores
from dashboard.theme import (
    ACCENT,
    LONG_COL,
    NEUTRAL,
    SHORT_COL,
    TEXT_MUTED,
    metric_card,
    section_header,
)
from data.db import (
    analyst_estimates,
    daily_prices,
    earnings_calendar,
    fundamentals,
    insider_transactions,
    short_interest,
    sp500_universe,
)
from factors.db import factor_scores
from portfolio.db import portfolio_positions


def render(engine, cfg: dict) -> None:  # noqa: ARG001
    # -----------------------------------------------------------------------
    # Ticker selector
    # -----------------------------------------------------------------------
    with engine.connect() as conn:
        universe_rows = conn.execute(
            sa.select(
                sp500_universe.c.ticker,
                sp500_universe.c.company_name,
                sp500_universe.c.gics_sector,
                sp500_universe.c.gics_sub_industry,
            ).order_by(sp500_universe.c.ticker)
        ).fetchall()

    if not universe_rows:
        st.info("Universe not loaded. Run `python run_data.py` first.")
        return

    options = [f"{r[0]}  —  {r[1]}" for r in universe_rows]
    ticker_map = {f"{r[0]}  —  {r[1]}": r for r in universe_rows}

    selected = st.selectbox("Ticker", options, label_visibility="collapsed")
    if not selected:
        return

    row = ticker_map[selected]
    ticker = row[0]
    company_name = row[1] or ""
    sector = row[2] or "—"
    sub_industry = row[3] or "—"

    # -----------------------------------------------------------------------
    # Load all data for this ticker in one connection
    # -----------------------------------------------------------------------
    with engine.connect() as conn:
        price_rows = conn.execute(
            sa.select(daily_prices)
            .where(daily_prices.c.ticker == ticker)
            .order_by(daily_prices.c.date)
        ).fetchall()

        factor_row = conn.execute(
            sa.select(factor_scores)
            .where(factor_scores.c.ticker == ticker)
            .order_by(factor_scores.c.score_date.desc())
            .limit(1)
        ).fetchone()

        ai_row = conn.execute(
            sa.select(ai_scores)
            .where(ai_scores.c.ticker == ticker)
            .order_by(ai_scores.c.score_date.desc())
            .limit(1)
        ).fetchone()

        fund_row = conn.execute(
            sa.select(fundamentals)
            .where(
                fundamentals.c.ticker == ticker,
                fundamentals.c.period_type == "annual",
            )
            .order_by(fundamentals.c.period_end.desc())
            .limit(1)
        ).fetchone()

        analyst_row = conn.execute(
            sa.select(analyst_estimates)
            .where(analyst_estimates.c.ticker == ticker)
            .order_by(analyst_estimates.c.date.desc())
            .limit(1)
        ).fetchone()

        si_row = conn.execute(
            sa.select(short_interest)
            .where(short_interest.c.ticker == ticker)
            .order_by(short_interest.c.date.desc())
            .limit(1)
        ).fetchone()

        insider_rows = conn.execute(
            sa.select(insider_transactions)
            .where(insider_transactions.c.ticker == ticker)
            .order_by(insider_transactions.c.date.desc())
            .limit(12)
        ).fetchall()

        position_row = conn.execute(
            sa.select(portfolio_positions).where(portfolio_positions.c.ticker == ticker)
        ).fetchone()

        next_earnings = conn.execute(
            sa.select(earnings_calendar)
            .where(earnings_calendar.c.ticker == ticker)
            .order_by(earnings_calendar.c.earnings_date.desc())
            .limit(1)
        ).fetchone()

    price_df = pd.DataFrame(price_rows, columns=daily_prices.columns.keys())

    # -----------------------------------------------------------------------
    # Header
    # -----------------------------------------------------------------------
    pos_badge = ""
    if position_row:
        pos = dict(zip(portfolio_positions.columns.keys(), position_row, strict=False))
        direction = pos.get("direction", "")
        if direction == "LONG":
            pos_badge = '&nbsp;<span class="long-badge">LONG</span>'
        elif direction == "SHORT":
            pos_badge = '&nbsp;<span class="short-badge">SHORT</span>'

    st.markdown(
        f'<h2 style="margin:0;font-size:1.6rem;font-weight:800;">{ticker}{pos_badge}</h2>'
        f'<p style="color:{TEXT_MUTED};margin:0.2rem 0 1rem;">'
        f"{company_name} &bull; {sector} &bull; {sub_industry}</p>",
        unsafe_allow_html=True,
    )

    # -----------------------------------------------------------------------
    # Section 1 — Closing Price Chart
    # -----------------------------------------------------------------------
    section_header("CLOSING PRICE")

    if price_df.empty:
        st.caption("No price data loaded for this ticker.")
    else:
        price_df["date"] = pd.to_datetime(price_df["date"])
        price_df = price_df.sort_values("date").reset_index(drop=True)

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=price_df["date"],
                y=price_df["adj_close"],
                name="Adj Close",
                line={"color": ACCENT, "width": 2},
                fill="tozeroy",
                fillcolor="rgba(99,102,241,0.08)",
            )
        )
        fig.update_layout(**_chart_layout(height=320, left_margin=60))
        fig.update_yaxes(title="USD")
        st.plotly_chart(fig, use_container_width=True)

        # Price KPIs
        latest_price = float(price_df["adj_close"].iloc[-1])
        tail_252 = price_df["adj_close"].tail(252)
        high_52w = float(tail_252.max())
        low_52w = float(tail_252.min())

        vol_series = price_df["volume"].dropna()
        avg_vol_30d = int(vol_series.tail(30).mean()) if not vol_series.empty else 0

        current_year = int(price_df["date"].dt.year.max())
        ytd_df = price_df[price_df["date"].dt.year == current_year]
        ret_ytd = (
            latest_price / float(ytd_df["adj_close"].iloc[0]) - 1 if len(ytd_df) > 1 else 0.0
        )

        k1, k2, k3, k4, k5 = st.columns(5)
        with k1:
            metric_card("Latest Close", f"${latest_price:.2f}")
        with k2:
            metric_card("52W High", f"${high_52w:.2f}", LONG_COL)
        with k3:
            metric_card("52W Low", f"${low_52w:.2f}", SHORT_COL)
        with k4:
            metric_card("30d Avg Vol", f"{avg_vol_30d:,}")
        with k5:
            metric_card(
                "YTD Return",
                f"{ret_ytd:+.1%}",
                LONG_COL if ret_ytd >= 0 else SHORT_COL,
            )

    if next_earnings:
        er = dict(zip(earnings_calendar.columns.keys(), next_earnings, strict=False))
        ed = er.get("earnings_date", "")
        eps_est = er.get("eps_estimate")
        note = f"Next/last earnings: {ed}"
        if eps_est is not None:
            note += f" | EPS estimate: ${eps_est:.2f}"
        st.caption(note)

    # -----------------------------------------------------------------------
    # Section 2 — Current Position (only if held)
    # -----------------------------------------------------------------------
    if position_row:
        section_header("CURRENT POSITION")
        pos = dict(zip(portfolio_positions.columns.keys(), position_row, strict=False))
        p1, p2, p3, p4, p5 = st.columns(5)
        direction = pos.get("direction", "—")
        with p1:
            metric_card(
                "Direction",
                direction,
                LONG_COL if direction == "LONG" else SHORT_COL,
            )
        with p2:
            metric_card("Shares", f"{pos.get('shares') or 0:,.0f}")
        with p3:
            metric_card("Entry Price", f"${pos.get('entry_price') or 0:.2f}")
        with p4:
            pnl = pos.get("unrealized_pnl") or 0
            metric_card("Unrealized P&L", f"${pnl:,.0f}", LONG_COL if pnl >= 0 else SHORT_COL)
        with p5:
            wt = pos.get("weight") or 0
            metric_card("Portfolio Weight", f"{wt:.1%}")

    # -----------------------------------------------------------------------
    # Section 3 — Factor Scores
    # -----------------------------------------------------------------------
    section_header("FACTOR SCORES")

    if factor_row is None:
        st.caption("No factor scores computed for this ticker.")
    else:
        fs = dict(zip(factor_scores.columns.keys(), factor_row, strict=False))
        st.caption(f"As of {fs.get('score_date', '—')}")

        score_cols = st.columns(8)
        factor_pairs = [
            ("Composite", "composite_score"),
            ("Momentum", "momentum_score"),
            ("Value", "value_score"),
            ("Quality", "quality_score"),
            ("Growth", "growth_score"),
            ("Revisions", "revisions_score"),
            ("Short Int.", "short_interest_score"),
            ("Insider", "insider_score"),
        ]
        for col, (label, key) in zip(score_cols, factor_pairs, strict=False):
            val = fs.get(key)
            with col:
                metric_card(label, f"{val:.0f}" if val is not None else "—", _score_colour(val))

        _render_factor_bars(fs)

    # -----------------------------------------------------------------------
    # Section 4 — AI Scores
    # -----------------------------------------------------------------------
    if ai_row:
        section_header("AI SCORES")
        ai = dict(zip(ai_scores.columns.keys(), ai_row, strict=False))
        st.caption(f"As of {ai.get('score_date', '—')}")
        a1, a2, a3, a4, a5 = st.columns(5)
        ai_pairs = [
            (a1, "Earnings", "earnings_score"),
            (a2, "Filing", "filing_score"),
            (a3, "Risk", "risk_score"),
            (a4, "Insider AI", "insider_ai_score"),
            (a5, "AI Composite", "ai_composite"),
        ]
        for col, label, key in ai_pairs:
            val = ai.get(key)
            with col:
                metric_card(label, f"{val:.0f}" if val is not None else "—", _score_colour(val))

    # -----------------------------------------------------------------------
    # Section 5 — Fundamentals
    # -----------------------------------------------------------------------
    section_header("FUNDAMENTALS (latest annual)")

    if fund_row is None:
        st.caption("No fundamental data loaded.")
    else:
        fd = dict(zip(fundamentals.columns.keys(), fund_row, strict=False))
        st.caption(f"Period ending {fd.get('period_end', '—')}")

        f1, f2, f3, f4 = st.columns(4)
        with f1:
            rev = fd.get("revenue")
            metric_card("Revenue", f"${rev / 1e9:.1f}B" if rev else "—")
        with f2:
            gm = fd.get("gross_margin")
            metric_card("Gross Margin", f"{gm:.1%}" if gm is not None else "—")
        with f3:
            om = fd.get("operating_margin")
            metric_card("Op. Margin", f"{om:.1%}" if om is not None else "—")
        with f4:
            roe = fd.get("roe")
            metric_card(
                "ROE",
                f"{roe:.1%}" if roe is not None else "—",
                LONG_COL if (roe or 0) > 0 else SHORT_COL,
            )

        f5, f6, f7, f8 = st.columns(4)
        with f5:
            de = fd.get("debt_to_equity")
            metric_card("Debt / Equity", f"{de:.2f}" if de is not None else "—")
        with f6:
            fcf = fd.get("fcf")
            metric_card(
                "FCF",
                f"${fcf / 1e9:.1f}B" if fcf else "—",
                LONG_COL if (fcf or 0) > 0 else NEUTRAL,
            )
        with f7:
            rev_g = fd.get("revenue_growth_yoy")
            metric_card(
                "Rev Growth YoY",
                f"{rev_g:+.1%}" if rev_g is not None else "—",
                LONG_COL if (rev_g or 0) > 0 else SHORT_COL,
            )
        with f8:
            cr = fd.get("current_ratio")
            metric_card("Current Ratio", f"{cr:.2f}" if cr is not None else "—")

    # -----------------------------------------------------------------------
    # Section 6 — Analyst Estimates & Short Interest
    # -----------------------------------------------------------------------
    left_col, right_col = st.columns(2)

    with left_col:
        section_header("ANALYST ESTIMATES")
        if analyst_row:
            ar = dict(zip(analyst_estimates.columns.keys(), analyst_row, strict=False))
            a1, a2, a3 = st.columns(3)
            with a1:
                pt = ar.get("price_target")
                metric_card("Price Target", f"${pt:.2f}" if pt else "—")
            with a2:
                eps = ar.get("eps_estimate_fwd")
                metric_card("Fwd EPS", f"${eps:.2f}" if eps else "—")
            with a3:
                na = ar.get("num_analysts")
                metric_card("# Analysts", str(na) if na else "—")
        else:
            st.caption("No analyst data.")

    with right_col:
        section_header("SHORT INTEREST")
        if si_row:
            si = dict(zip(short_interest.columns.keys(), si_row, strict=False))
            s1, s2, s3 = st.columns(3)
            with s1:
                pf = si.get("short_pct_float")
                metric_card(
                    "% Float Short",
                    f"{pf:.1%}" if pf is not None else "—",
                    SHORT_COL if (pf or 0) > 0.10 else NEUTRAL,
                )
            with s2:
                dtc = si.get("short_ratio")
                metric_card("Days to Cover", f"{dtc:.1f}" if dtc is not None else "—")
            with s3:
                ss = si.get("shares_short")
                metric_card("Shares Short", f"{ss / 1e6:.1f}M" if ss else "—")
        else:
            st.caption("No short interest data.")

    # -----------------------------------------------------------------------
    # Section 7 — Recent Insider Transactions
    # -----------------------------------------------------------------------
    section_header("RECENT INSIDER TRANSACTIONS")

    if insider_rows:
        ins_df = pd.DataFrame(insider_rows, columns=insider_transactions.columns.keys())
        disp = ins_df[
            ["date", "insider_name", "insider_title", "transaction_type", "shares", "price"]
        ].rename(
            columns={
                "date": "Date",
                "insider_name": "Name",
                "insider_title": "Title",
                "transaction_type": "Type",
                "shares": "Shares",
                "price": "Price",
            }
        )
        st.dataframe(disp, use_container_width=True, hide_index=True)
    else:
        st.caption("No insider transaction data.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _score_colour(val: float | None) -> str:
    if val is None:
        return NEUTRAL
    if val >= 60:
        return LONG_COL
    if val <= 40:
        return SHORT_COL
    return NEUTRAL


def _render_factor_bars(fs: dict) -> None:
    """Horizontal bar chart of the eight factor composite scores (0–100 percentile)."""
    labels = [
        "Institutional",
        "Insider",
        "Short Interest",
        "Revisions",
        "Growth",
        "Quality",
        "Value",
        "Momentum",
    ]
    keys = [
        "institutional_score",
        "insider_score",
        "short_interest_score",
        "revisions_score",
        "growth_score",
        "quality_score",
        "value_score",
        "momentum_score",
    ]
    values = [fs.get(k) or 50.0 for k in keys]
    colours = [_score_colour(v) for v in values]

    fig = go.Figure(
        go.Bar(
            x=values,
            y=labels,
            orientation="h",
            marker_color=colours,
            text=[f"{v:.0f}" for v in values],
            textposition="outside",
            cliponaxis=False,
        )
    )
    fig.add_vline(x=50, line_dash="dash", line_color="rgba(255,255,255,0.2)")
    fig.update_layout(**_chart_layout(height=280, left_margin=120), showlegend=False)
    fig.update_xaxes(range=[0, 110], gridcolor="rgba(255,255,255,0.05)")
    st.plotly_chart(fig, use_container_width=True)


def _chart_layout(height: int = 300, left_margin: int = 50) -> dict:
    return {
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "font_color": "#e2e8f0",
        "height": height,
        "margin": {"t": 10, "b": 30, "l": left_margin, "r": 60},
        "legend": {
            "bgcolor": "rgba(0,0,0,0)",
            "bordercolor": "rgba(99,102,241,0.2)",
            "borderwidth": 1,
        },
        "xaxis": {"gridcolor": "rgba(255,255,255,0.05)"},
        "yaxis": {"gridcolor": "rgba(255,255,255,0.05)"},
    }
