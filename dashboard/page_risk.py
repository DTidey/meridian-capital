"""Page III — Risk: circuit breakers, tail risk, factor decomposition, stress tests."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import sqlalchemy as sa
import streamlit as st

from dashboard.theme import ACCENT, LONG_COL, NEUTRAL, SHORT_COL, TEXT_MUTED, metric_card, section_header
from data.db import daily_prices
from portfolio.db import portfolio_positions
from reporting.db import portfolio_nav
from risk.db import risk_events, risk_log


def render(engine, cfg: dict) -> None:
    risk_cfg = cfg.get("risk", {})
    cb_cfg   = risk_cfg.get("circuit_breakers", {})

    today    = date.today().isoformat()
    cutoff72 = (date.today() - timedelta(hours=72)).isoformat()

    with engine.connect() as conn:
        nav_rows = conn.execute(
            sa.select(portfolio_nav).order_by(portfolio_nav.c.date.desc()).limit(10)
        ).fetchall()

        pos_rows = conn.execute(
            sa.select(
                portfolio_positions.c.ticker,
                portfolio_positions.c.direction,
                portfolio_positions.c.weight,
                portfolio_positions.c.sector,
                portfolio_positions.c.beta,
            )
        ).fetchall()

        vix_row = conn.execute(
            sa.select(daily_prices.c.adj_close, daily_prices.c.date)
            .where(daily_prices.c.ticker == "^VIX")
            .order_by(daily_prices.c.date.desc()).limit(1)
        ).fetchone()

        hyg_row = conn.execute(
            sa.select(daily_prices.c.adj_close)
            .where(daily_prices.c.ticker == "HYG")
            .order_by(daily_prices.c.date.desc()).limit(1)
        ).fetchone()

        tlt_row = conn.execute(
            sa.select(daily_prices.c.adj_close)
            .where(daily_prices.c.ticker == "TLT")
            .order_by(daily_prices.c.date.desc()).limit(1)
        ).fetchone()

        alert_rows = conn.execute(
            sa.select(risk_log)
            .where(
                risk_log.c.recorded_at >= cutoff72,
                risk_log.c.result.in_(["WARNING", "REJECTED", "TRIGGERED"]),
            )
            .order_by(risk_log.c.recorded_at.desc())
        ).fetchall()

        event_rows = conn.execute(
            sa.select(risk_events)
            .where(risk_events.c.event_date == today)
            .order_by(risk_events.c.recorded_at.desc())
        ).fetchall()

    nav_df = pd.DataFrame(nav_rows, columns=portfolio_nav.columns.keys())

    # -----------------------------------------------------------------------
    # Section 1 — Circuit Breaker Bars
    # -----------------------------------------------------------------------
    section_header("CIRCUIT BREAKERS")

    latest_nav = nav_df.iloc[0] if not nav_df.empty else None
    drawdown   = float(latest_nav["drawdown_pct"]) if latest_nav is not None else 0.0

    # Daily / weekly P&L from nav history
    daily_loss  = 0.0
    weekly_loss = 0.0
    if len(nav_df) >= 2:
        n_curr = float(nav_df.iloc[0]["nav"])
        n_prev = float(nav_df.iloc[1]["nav"])
        if n_prev > 0:
            daily_loss = (n_curr - n_prev) / n_prev
    if len(nav_df) >= 6:
        n_week = float(nav_df.iloc[5]["nav"])
        if n_week > 0:
            weekly_loss = (n_curr - n_week) / n_week

    daily_pct   = -min(daily_loss, 0)
    weekly_pct  = -min(weekly_loss, 0)

    cb_daily_warn   = float(cb_cfg.get("daily_size_down",  0.015))
    cb_daily_crit   = float(cb_cfg.get("daily_close_all",  0.025))
    cb_weekly       = float(cb_cfg.get("weekly_size_down", 0.040))
    cb_drawdown     = float(cb_cfg.get("drawdown_kill",    0.080))

    for label, val, warn, crit in [
        ("Daily Loss",   daily_pct,  cb_daily_warn, cb_daily_crit),
        ("Weekly Loss",  weekly_pct, cb_weekly,     cb_weekly),
        ("Max Drawdown", drawdown,   cb_drawdown * 0.5, cb_drawdown),
    ]:
        pct = min(val / max(crit, 0.001), 1.0)
        colour = SHORT_COL if pct >= 0.9 else ("#f59e0b" if pct >= 0.5 else LONG_COL)
        st.markdown(
            f'<div style="margin-bottom:0.5rem;">'
            f'<div style="font-size:0.7rem;color:{TEXT_MUTED};margin-bottom:0.2rem;">'
            f'{label}: {val:.2%} / limit {crit:.2%}</div>'
            f'<div style="background:#1e2535;border-radius:999px;height:8px;">'
            f'<div style="background:{colour};border-radius:999px;height:8px;'
            f'width:{pct*100:.1f}%;transition:width 0.3s;"></div>'
            f'</div></div>',
            unsafe_allow_html=True,
        )

    # -----------------------------------------------------------------------
    # Section 2 — Tail Risk KPIs
    # -----------------------------------------------------------------------
    section_header("TAIL RISK")
    vix   = float(vix_row[0]) if vix_row else None
    hyg   = float(hyg_row[0]) if hyg_row else None
    tlt   = float(tlt_row[0]) if tlt_row else None
    spread = (tlt - hyg) if (hyg and tlt) else None

    cols = st.columns(4)
    with cols[0]: metric_card("VIX", f"{vix:.1f}" if vix else "—", SHORT_COL if vix and vix > 25 else NEUTRAL)
    with cols[1]: metric_card("HYG", f"{hyg:.2f}" if hyg else "—")
    with cols[2]: metric_card("TLT", f"{tlt:.2f}" if tlt else "—")
    with cols[3]: metric_card("HYG-TLT Spread", f"{spread:.2f}" if spread is not None else "—")

    # -----------------------------------------------------------------------
    # Section 3 — Risk Decomposition Donut (from cached parquet)
    # -----------------------------------------------------------------------
    section_header("RISK DECOMPOSITION")
    cov_path = Path("cache") / "predicted_cov_latest.parquet"
    left_col, right_col = st.columns(2)

    if cov_path.exists():
        try:
            cov_meta = pd.read_parquet(cov_path)
            factor_var_pct   = float(cov_meta.get("factor_var_pct",   pd.Series([0.0])).iloc[0])
            specific_var_pct = float(cov_meta.get("specific_var_pct", pd.Series([0.0])).iloc[0])
            with left_col:
                fig = go.Figure(go.Pie(
                    labels=["Factor Risk", "Specific Risk"],
                    values=[factor_var_pct, specific_var_pct],
                    marker_colors=[ACCENT, SHORT_COL],
                    hole=0.55,
                ))
                fig.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font_color="#e2e8f0", height=260, margin=dict(t=10,b=10,l=10,r=10),
                    showlegend=True,
                )
                st.plotly_chart(fig, use_container_width=True)
        except Exception as e:
            with left_col:
                st.caption(f"Covariance cache unavailable: {e}")
    else:
        with left_col:
            st.caption("Run `python run_risk_check.py` to generate covariance cache.")

    # -----------------------------------------------------------------------
    # Section 4 — Factor Exposure Bars
    # -----------------------------------------------------------------------
    section_header("FACTOR EXPOSURES")
    from factors.db import factor_scores as fs_table
    _FACTOR_SCORE_COLS = [
        "momentum_score", "quality_score", "value_score", "growth_score",
        "revisions_score", "insider_score", "short_interest_score", "institutional_score",
    ]
    threshold = float(risk_cfg.get("factor_monitor", {}).get("alert_z_threshold", 1.5))

    with engine.connect() as conn:
        latest_sd = conn.execute(sa.select(sa.func.max(fs_table.c.score_date))).scalar()
        if latest_sd:
            score_rows = conn.execute(
                sa.select(fs_table.c.ticker, *[fs_table.c[c] for c in _FACTOR_SCORE_COLS])
                .where(fs_table.c.score_date == latest_sd)
            ).fetchall()
        else:
            score_rows = []

    if score_rows and pos_rows:
        pos_weights = {r[0]: abs(r[2]) for r in pos_rows}
        total_w = sum(pos_weights.values()) or 1.0
        score_df = pd.DataFrame(score_rows, columns=["ticker"] + _FACTOR_SCORE_COLS)
        score_df = score_df[score_df["ticker"].isin(pos_weights)]
        score_df["w"] = score_df["ticker"].map(pos_weights) / total_w
        for col in _FACTOR_SCORE_COLS:
            score_df[col] = pd.to_numeric(score_df[col], errors="coerce").fillna(50.0)

        factor_z = {}
        for col in _FACTOR_SCORE_COLS:
            wmean = float((score_df[col] * score_df["w"]).sum())
            factor_z[col.replace("_score", "").replace("_", " ").title()] = (wmean - 50) / 25

        fig = go.Figure()
        for fname, z in factor_z.items():
            colour = SHORT_COL if abs(z) > threshold else ACCENT
            fig.add_bar(x=[z], y=[fname], orientation="h",
                        marker_color=colour, name=fname)
        fig.add_vline(x=0, line_width=1, line_color=TEXT_MUTED)
        fig.add_vline(x=threshold,  line_dash="dot", line_color="#f59e0b")
        fig.add_vline(x=-threshold, line_dash="dot", line_color="#f59e0b")
        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font_color="#e2e8f0", height=320, showlegend=False,
            margin=dict(t=10,b=10,l=120,r=30),
            xaxis_title="Weighted z-score",
        )
        st.plotly_chart(fig, use_container_width=True)

    # -----------------------------------------------------------------------
    # Section 5 — Stress Test Table
    # -----------------------------------------------------------------------
    section_header("STRESS TESTS")
    with engine.connect() as conn:
        stress_rows = conn.execute(
            sa.select(risk_log)
            .where(risk_log.c.check_type == "stress")
            .order_by(risk_log.c.recorded_at.desc())
            .limit(12)
        ).fetchall()

    if stress_rows:
        stress_df = pd.DataFrame(stress_rows, columns=risk_log.columns.keys())
        st.dataframe(
            stress_df[["run_date", "ticker", "result", "reason"]],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("Run `python run_risk_check.py --stress` to populate stress test results.")

    # -----------------------------------------------------------------------
    # Section 6 — Correlation Heatmap
    # -----------------------------------------------------------------------
    section_header("CORRELATION HEATMAP (60d)")
    cutoff60 = (date.today() - timedelta(days=60)).isoformat()
    held_tickers = [r[0] for r in pos_rows]

    if held_tickers:
        with engine.connect() as conn:
            price_rows = conn.execute(
                sa.select(daily_prices.c.date, daily_prices.c.ticker, daily_prices.c.adj_close)
                .where(
                    daily_prices.c.ticker.in_(held_tickers),
                    daily_prices.c.date >= cutoff60,
                ).order_by(daily_prices.c.date)
            ).fetchall()

        if price_rows:
            price_df = pd.DataFrame(price_rows, columns=["date", "ticker", "close"])
            pivot    = price_df.pivot(index="date", columns="ticker", values="close")
            corr     = pivot.pct_change().corr()

            fig = px.imshow(
                corr,
                color_continuous_scale="RdBu_r",
                zmin=-1, zmax=1,
                aspect="auto",
            )
            fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font_color="#e2e8f0", height=400, margin=dict(t=20,b=20,l=20,r=20),
                coloraxis_showscale=True,
            )
            st.plotly_chart(fig, use_container_width=True)

            if corr.size > 1:
                upper = corr.where(np.triu(np.ones(corr.shape), k=1).astype(bool))
                avg_corr = float(upper.stack().abs().mean())
                eff_bets = round(1 / avg_corr, 1) if avg_corr > 0 else 0
                st.caption(f"Avg pairwise |corr|: {avg_corr:.2f} — Effective bets: {eff_bets}")

    # -----------------------------------------------------------------------
    # Section 7 — 72-Hour Alerts
    # -----------------------------------------------------------------------
    section_header("72-HOUR ALERTS")
    if alert_rows:
        for row in alert_rows:
            d = dict(row._mapping)
            colour = SHORT_COL if d["result"] == "TRIGGERED" else "#f59e0b"
            st.markdown(
                f'<div style="border-left:3px solid {colour};padding:0.4rem 0.8rem;'
                f'margin-bottom:0.4rem;background:rgba(0,0,0,0.2);border-radius:0 6px 6px 0;">'
                f'<span style="color:{colour};font-weight:700;">{d["result"]}</span> '
                f'· <span style="color:{TEXT_MUTED};font-size:0.75rem;">{d["recorded_at"][:16]}</span> '
                f'· {d.get("check_type","")} {("— " + d["reason"]) if d.get("reason") else ""}'
                f'</div>',
                unsafe_allow_html=True,
            )
    else:
        st.caption("No alerts in the last 72 hours.")
