"""Page I — Portfolio (Cover): JARVIS branding, KPIs, status strip, chat."""

from __future__ import annotations

from datetime import date, timedelta

import sqlalchemy as sa
import streamlit as st

from dashboard.jarvis import build_snapshot, render_chat
from dashboard.theme import (
    ACCENT, CARD_GRAD_A, CARD_GRAD_B, DARK_BG, LONG_COL,
    NEUTRAL, SHORT_COL, TEXT_MUTED, TEXT_PRIMARY,
    inject_css, metric_card, section_header, vix_badge,
)
from data.db import daily_prices, earnings_calendar, insider_cluster_flags, insider_transactions, sp500_universe
from factors.db import factor_scores as factor_scores_table
from portfolio.db import portfolio_positions
from reporting.db import portfolio_nav


def render(engine, cfg: dict) -> None:
    snap = build_snapshot(engine)

    left, right = st.columns([0.44, 0.56])

    # -----------------------------------------------------------------------
    # LEFT — branding, KPIs, status, chat
    # -----------------------------------------------------------------------
    with left:
        st.markdown(
            f'<div style="font-size:92px;font-weight:800;line-height:1;'
            f'background:linear-gradient(135deg,{ACCENT},#818cf8);'
            f'-webkit-background-clip:text;-webkit-text-fill-color:transparent;'
            f'margin-bottom:0.1rem;">JARVIS</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div style="font-size:11px;font-weight:700;letter-spacing:0.25em;'
            f'text-transform:uppercase;color:{TEXT_MUTED};margin-bottom:1.5rem;">'
            f'LONG / SHORT HEDGE FUND ANALYST</div>',
            unsafe_allow_html=True,
        )

        section_header("SYSTEM METRICS")

        vix      = snap.get("vix")
        nav_usd  = snap.get("nav_usd", 0.0)
        nav_chg  = snap.get("nav_change_1d", 0.0)
        nav_col  = LONG_COL if nav_chg >= 0 else SHORT_COL

        kpi_data = [
            ("Universe",          str(snap.get("universe_size", "—")),     NEUTRAL),
            ("Long Candidates",   str(snap.get("long_candidates", "—")),   LONG_COL),
            ("Short Candidates",  str(snap.get("short_candidates", "—")),  SHORT_COL),
            ("Positions",         str(snap.get("positions_count", "—")),   NEUTRAL),
            ("Crowding Flags",    str(len(snap.get("crowding_flags", []))), "#f59e0b"),
            ("Insider Events 30d",str(snap.get("insider_events_30d", "—")),NEUTRAL),
            ("CEO Buys 30d",      str(snap.get("ceo_buys_30d", "—")),      LONG_COL),
            ("Cluster Buys",      str(snap.get("cluster_buys_active", "—")),LONG_COL),
            ("VIX",               f"{vix:.1f}" if vix else "—",            "#f59e0b" if vix and vix > 15 else LONG_COL),
            ("Earnings 7d",       str(len(snap.get("earnings_7d", []))),   "#f59e0b"),
        ]

        cols_per_row = 2
        kpi_rows = [kpi_data[i:i+cols_per_row] for i in range(0, len(kpi_data), cols_per_row)]
        for row in kpi_rows:
            cols = st.columns(cols_per_row)
            for col, (label, value, colour) in zip(cols, row):
                with col:
                    metric_card(label, value, colour)

        # Status strip
        regime    = snap.get("vix_regime", "UNKNOWN")
        price_dt  = snap.get("data_freshness", {}).get("prices")
        today_str = date.today().isoformat()
        fresh     = price_dt == today_str if price_dt else False

        regime_colours = {"LOW": LONG_COL, "CAUTION": "#f59e0b", "STRESS": SHORT_COL, "UNKNOWN": NEUTRAL}
        regime_col     = regime_colours.get(regime, NEUTRAL)
        data_label     = "● LIVE" if fresh else f"● DELAYED ({_days_ago(price_dt)}d)"
        data_col       = LONG_COL if fresh else "#f59e0b"

        if snap.get("halt_lock_active"):
            st.markdown(
                f'<div style="background:rgba(244,63,94,0.12);border:1px solid {SHORT_COL};'
                f'border-radius:8px;padding:0.5rem 1rem;margin:0.8rem 0;font-weight:700;color:{SHORT_COL};">'
                f'⛔ HALT LOCK ACTIVE</div>',
                unsafe_allow_html=True,
            )

        st.markdown(
            f'<div style="display:flex;gap:1rem;margin:0.8rem 0;align-items:center;">'
            f'<span style="font-size:0.7rem;font-weight:700;color:{regime_col};'
            f'border:1px solid {regime_col};border-radius:999px;padding:0.2rem 0.7rem;">'
            f'VIX {regime}</span>'
            f'<span style="font-size:0.7rem;font-weight:700;color:{data_col};">{data_label}</span>'
            f'<span style="font-size:0.7rem;color:{NEUTRAL};">NAV: ${nav_usd:,.0f} '
            f'<span style="color:{nav_col};">({nav_chg:+.2%})</span></span>'
            f'</div>',
            unsafe_allow_html=True,
        )

        section_header("ASK JARVIS")
        render_chat(engine, snap)

    # -----------------------------------------------------------------------
    # RIGHT — gradient panel
    # -----------------------------------------------------------------------
    with right:
        from pathlib import Path
        robot_path = Path(__file__).parent.parent / "assets" / "robot.png"

        st.markdown(
            f'<div style="background:linear-gradient(160deg,{CARD_GRAD_A},{CARD_GRAD_B},'
            f'#0f1626);border-radius:16px;min-height:600px;display:flex;'
            f'align-items:center;justify-content:center;padding:2rem;">'
            + (
                f'<img src="data:image/png;base64,{_img_b64(robot_path)}" '
                f'style="max-height:500px;opacity:0.85;" />'
                if robot_path.exists()
                else f'<div style="text-align:center;">'
                     f'<div style="font-size:8rem;opacity:0.15;">⚡</div>'
                     f'<div style="font-size:0.75rem;color:{TEXT_MUTED};letter-spacing:0.2em;">'
                     f'MERIDIAN CAPITAL PARTNERS</div></div>'
            )
            + '</div>',
            unsafe_allow_html=True,
        )


def _days_ago(date_str: str | None) -> int:
    if not date_str:
        return 999
    try:
        return (date.today() - date.fromisoformat(date_str)).days
    except Exception:
        return 999


def _img_b64(path) -> str:
    import base64
    return base64.b64encode(path.read_bytes()).decode()
