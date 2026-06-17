"""Page IV — Performance: equity curve, monthly grid, attribution, win/loss."""

from __future__ import annotations

import math

import pandas as pd
import plotly.graph_objects as go
import sqlalchemy as sa
import streamlit as st

from dashboard.theme import (
    ACCENT,
    LONG_COL,
    NEUTRAL,
    SHORT_COL,
    TEXT_MUTED,
    metric_card,
    section_header,
)
from reporting.db import pnl_attribution, portfolio_nav, position_trades, weekly_commentary
from reporting.sector_performance import compute as sector_compute
from reporting.turnover import compute as turnover_compute
from reporting.win_loss import compute as win_loss_compute


def render(engine, cfg: dict) -> None:
    with engine.connect() as conn:
        nav_rows = conn.execute(sa.select(portfolio_nav).order_by(portfolio_nav.c.date)).fetchall()
        attr_rows = conn.execute(
            sa.select(pnl_attribution).order_by(pnl_attribution.c.date)
        ).fetchall()

    nav_df = pd.DataFrame(nav_rows, columns=portfolio_nav.columns.keys())
    attr_df = pd.DataFrame(attr_rows, columns=pnl_attribution.columns.keys())

    if nav_df.empty:
        st.info("No NAV data yet. Run `python run_reporting.py` first.")
        return

    # -----------------------------------------------------------------------
    # Section 1 — Equity Curve
    # -----------------------------------------------------------------------
    section_header("EQUITY CURVE VS SPY (REBASED TO 100)")
    nav_df["date"] = pd.to_datetime(nav_df["date"])
    nav_df["port_idx"] = nav_df["nav"] / nav_df["nav"].iloc[0] * 100
    spy_clean = nav_df[nav_df["spy_close"].notna()].copy()
    if not spy_clean.empty:
        spy_base = spy_clean["spy_close"].iloc[0]
        nav_df["spy_idx"] = nav_df["spy_close"] / spy_base * 100

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=nav_df["date"],
            y=nav_df["port_idx"],
            name="Portfolio",
            line={"color": ACCENT, "width": 2},
        )
    )
    if "spy_idx" in nav_df.columns:
        fig.add_trace(
            go.Scatter(
                x=nav_df["date"],
                y=nav_df["spy_idx"],
                name="SPY",
                line={"color": NEUTRAL, "width": 1.5, "dash": "dot"},
            )
        )
    fig.update_layout(**_plot_layout(height=300))
    st.plotly_chart(fig, use_container_width=True)

    # -----------------------------------------------------------------------
    # Section 2 — Monthly Returns Grid
    # -----------------------------------------------------------------------
    section_header("MONTHLY RETURNS")
    daily_rets = nav_df.set_index("date")["nav"].pct_change().dropna()
    if not daily_rets.empty:
        monthly = (1 + daily_rets).resample("ME").prod() - 1
        years = sorted(monthly.index.year.unique())
        data = {}
        for yr in years:
            row = {}
            annual = 1.0
            for m in range(1, 13):
                vals = monthly[(monthly.index.year == yr) & (monthly.index.month == m)]
                if not vals.empty:
                    v = float(vals.iloc[0])
                    row[f"{m:02d}"] = v
                    annual *= 1 + v
                else:
                    row[f"{m:02d}"] = None
            row["Annual"] = annual - 1.0
            data[yr] = row
        grid = pd.DataFrame(data).T
        grid.index.name = "Year"

        def _colour(val):
            if val is None or (isinstance(val, float) and math.isnan(val)):
                return ""
            return (
                f"background-color: {'rgba(16,185,129,0.2)' if val >= 0 else 'rgba(244,63,94,0.2)'}"
            )

        styled = grid.style.map(_colour).format(
            lambda v: (
                f"{v:+.1%}"
                if v is not None and not (isinstance(v, float) and math.isnan(v))
                else "—"
            )
        )
        st.dataframe(styled, use_container_width=True)

    # -----------------------------------------------------------------------
    # Section 3 — Drawdown
    # -----------------------------------------------------------------------
    section_header("DRAWDOWN")
    fig2 = go.Figure()
    fig2.add_trace(
        go.Scatter(
            x=nav_df["date"],
            y=-nav_df["drawdown_pct"] * 100,
            fill="tozeroy",
            fillcolor="rgba(244,63,94,0.15)",
            line={"color": SHORT_COL, "width": 1.5},
            name="Drawdown %",
        )
    )
    fig2.update_layout(**_plot_layout(height=200), yaxis_title="Drawdown (%)")
    st.plotly_chart(fig2, use_container_width=True)

    # -----------------------------------------------------------------------
    # Section 4 — P&L Attribution
    # -----------------------------------------------------------------------
    section_header("P&L ATTRIBUTION (last 90d)")
    if not attr_df.empty:
        attr_df["date"] = pd.to_datetime(attr_df["date"])
        attr_90 = attr_df[attr_df["date"] >= pd.Timestamp.now() - pd.Timedelta(days=90)]
        fig3 = go.Figure()
        for col, colour, label in [
            ("beta_pnl", "#6366f1", "Beta"),
            ("sector_pnl", "#06b6d4", "Sector"),
            ("factor_pnl", "#8b5cf6", "Factor"),
            ("alpha_pnl", LONG_COL, "Alpha"),
        ]:
            fig3.add_trace(
                go.Bar(
                    x=attr_90["date"],
                    y=attr_90[col] * 100,
                    name=label,
                    marker_color=colour,
                )
            )
        fig3.update_layout(**_plot_layout(height=280), barmode="stack", yaxis_title="Return (%)")
        st.plotly_chart(fig3, use_container_width=True)

    # -----------------------------------------------------------------------
    # Section 5 — Rolling 12-Month Sharpe
    # -----------------------------------------------------------------------
    section_header("ROLLING 12-MONTH SHARPE")
    if len(daily_rets) >= 252:
        roll = (daily_rets.rolling(252).mean() * 252 - 0.05) / (
            daily_rets.rolling(252).std() * math.sqrt(252)
        )
        roll = roll.dropna().reset_index()
        roll.columns = ["date", "sharpe"]
        fig4 = go.Figure()
        fig4.add_trace(
            go.Scatter(x=roll["date"], y=roll["sharpe"], name="Sharpe", line={"color": ACCENT})
        )
        fig4.add_hline(y=0, line_dash="dash", line_color=TEXT_MUTED)
        fig4.add_hline(y=1.0, line_dash="dot", line_color=LONG_COL)
        fig4.update_layout(**_plot_layout(height=220), yaxis_title="Sharpe")
        st.plotly_chart(fig4, use_container_width=True)
    else:
        st.caption("Need ≥252 trading days for rolling Sharpe.")

    # -----------------------------------------------------------------------
    # Section 6 — Sector-Relative Alpha
    # -----------------------------------------------------------------------
    section_header("SECTOR-RELATIVE ALPHA (90d)")
    try:
        etf_map = cfg.get("scoring", {}).get("sector_etf_map")
        sec_df = sector_compute(engine, lookback_days=90, sector_etf_map=etf_map or None)
        if not sec_df.empty:
            total_alpha = float(sec_df["alpha"].sum())
            winner_count = int(sec_df["winner"].sum())
            loser_count = int((~sec_df["winner"]).sum())

            cols = st.columns(3)
            with cols[0]:
                metric_card(
                    "Total Alpha (90d)",
                    f"{total_alpha:+.2%}",
                    LONG_COL if total_alpha >= 0 else SHORT_COL,
                )
            with cols[1]:
                metric_card("Winner Sectors", str(winner_count), LONG_COL)
            with cols[2]:
                metric_card("Loser Sectors", str(loser_count), SHORT_COL)

            fig5 = go.Figure(
                go.Bar(
                    x=sec_df["sector"],
                    y=sec_df["alpha"] * 100,
                    marker_color=[LONG_COL if a >= 0 else SHORT_COL for a in sec_df["alpha"]],
                )
            )
            fig5.update_layout(**_plot_layout(height=260), yaxis_title="Alpha (%)")
            st.plotly_chart(fig5, use_container_width=True)
    except Exception as e:
        st.caption(f"Sector alpha unavailable: {e}")

    # -----------------------------------------------------------------------
    # Section 7 — Turnover Panel
    # -----------------------------------------------------------------------
    section_header("TURNOVER & TAX")
    try:
        tv = turnover_compute(engine, (cfg.get("portfolio") or {}).get("turnover_budget_pct", 0.30))
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            metric_card("30d Turnover", f"{tv['turnover_30d_pct']:.1%}")
        with c2:
            metric_card("Annualised", f"{tv['turnover_annualized']:.1%}")
        with c3:
            metric_card("Budget", f"{tv['budget_pct']:.1%}", NEUTRAL)
        with c4:
            metric_card("Tax Estimate", f"${tv['tax_estimate_usd']:,.0f}", SHORT_COL)
    except Exception as e:
        st.caption(f"Turnover unavailable: {e}")

    # -----------------------------------------------------------------------
    # Section 8 — Best / Worst 5
    # -----------------------------------------------------------------------
    section_header("BEST / WORST CONTRIBUTORS")
    with engine.connect() as conn:
        trade_rows = conn.execute(
            sa.select(position_trades)
            .where(position_trades.c.exit_date.isnot(None))
            .order_by(position_trades.c.realized_pnl.desc())
        ).fetchall()

    if trade_rows:
        tr_df = pd.DataFrame(trade_rows, columns=position_trades.columns.keys())
        disp_cols = [
            "ticker",
            "direction",
            "holding_days",
            "entry_price",
            "exit_price",
            "realized_pnl",
        ]
        c_left, c_right = st.columns(2)
        with c_left:
            st.caption("Best 5")
            st.dataframe(tr_df.head(5)[disp_cols], use_container_width=True, hide_index=True)
        with c_right:
            st.caption("Worst 5")
            st.dataframe(tr_df.tail(5)[disp_cols], use_container_width=True, hide_index=True)

    # -----------------------------------------------------------------------
    # Section 9 — Win/Loss Panel
    # -----------------------------------------------------------------------
    section_header("WIN / LOSS ANALYSIS")
    try:
        wl = win_loss_compute(engine)
        overall = wl.get("overall", {})
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            metric_card("Win Rate", f"{overall.get('win_rate', 0):.1%}", LONG_COL)
        with c2:
            metric_card("P/L Ratio", f"{overall.get('pl_ratio', 0):.2f}")
        with c3:
            metric_card("Avg Win", f"${overall.get('avg_win', 0):,.0f}", LONG_COL)
        with c4:
            metric_card("Avg Loss", f"${overall.get('avg_loss', 0):,.0f}", SHORT_COL)

        tabs = st.tabs(
            ["By Side", "By Holding Period", "By Sector", "By VIX Regime", "By Quintile"]
        )
        _wl_tab(tabs[0], wl.get("by_side", {}))
        _wl_tab(tabs[1], wl.get("by_holding_period", {}))
        _wl_tab(tabs[2], wl.get("by_sector", {}))
        _wl_tab(tabs[3], wl.get("by_vix_regime", {}))
        _wl_tab(tabs[4], {str(k): v for k, v in wl.get("by_factor_quintile", {}).items()})

        streaks = wl.get("streaks", {})
        st.caption(
            f"Streak: {streaks.get('current_streak', '—')} | "
            f"Best win streak: {streaks.get('longest_win_streak', 0)} | "
            f"Worst loss streak: {streaks.get('longest_loss_streak', 0)}"
        )
    except Exception as e:
        st.caption(f"Win/loss unavailable: {e}")

    # -----------------------------------------------------------------------
    # Section 10 — Weekly Commentary
    # -----------------------------------------------------------------------
    section_header("JARVIS WEEKLY COMMENTARY")
    with engine.connect() as conn:
        comment = conn.execute(
            sa.select(weekly_commentary).order_by(weekly_commentary.c.week_start.desc()).limit(1)
        ).fetchone()

    if comment:
        week_of = comment[0]
        content = comment[1]
        st.markdown(f"*Week of {week_of}*")
        st.markdown(
            f'<div class="card">{content}</div>',
            unsafe_allow_html=True,
        )
        if st.button("↺ Regenerate Commentary"):
            from pathlib import Path

            import yaml

            from reporting.commentary import generate_if_due

            with open(Path(__file__).parent.parent / "config.yaml") as fh:
                cfg2 = yaml.safe_load(fh)
            with st.spinner("JARVIS is thinking…"):
                generate_if_due(engine, cfg=cfg2, force=True)
            st.rerun()
    else:
        st.caption("No commentary yet. Run `python run_reporting.py --commentary`.")


def _wl_tab(tab, data: dict) -> None:
    with tab:
        if not data:
            st.caption("No data.")
            return
        rows = []
        for key, stats in data.items():
            rows.append(
                {
                    "Category": str(key),
                    "Win Rate": f"{stats.get('win_rate', 0):.1%}",
                    "P/L Ratio": f"{stats.get('pl_ratio', 0):.2f}",
                    "Avg Win $": f"${stats.get('avg_win', 0):,.0f}",
                    "Avg Loss $": f"${stats.get('avg_loss', 0):,.0f}",
                    "Trades": stats.get("total_trades", 0),
                }
            )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _plot_layout(height: int = 300) -> dict:
    return {
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "font_color": "#e2e8f0",
        "height": height,
        "margin": {"t": 10, "b": 30, "l": 50, "r": 20},
        "legend": {
            "bgcolor": "rgba(0,0,0,0)",
            "bordercolor": "rgba(99,102,241,0.2)",
            "borderwidth": 1,
        },
        "xaxis": {"gridcolor": "rgba(255,255,255,0.05)"},
        "yaxis": {"gridcolor": "rgba(255,255,255,0.05)"},
    }
