"""JARVIS snapshot builder and OpenAI streaming chat widget."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

import sqlalchemy as sa
import streamlit as st

from data.db import daily_prices, earnings_calendar, insider_cluster_flags, insider_transactions, sp500_universe
from factors.db import factor_scores as factor_scores_table
from portfolio.db import portfolio_positions
from reporting.db import pnl_attribution, portfolio_nav

if TYPE_CHECKING:
    import sqlalchemy.engine

log = logging.getLogger(__name__)

_SNAPSHOT_TTL = 60  # seconds
_CHAT_MAX_TURNS = 6

_JARVIS_SYSTEM = """You are JARVIS — the portfolio intelligence system for Meridian Capital Partners, \
a quantitative long/short equity hedge fund. You speak with authority, precision, and a dry wit. \
You reference specific positions, factor scores, and risk metrics. You never hedge with "I think" \
or "perhaps". You write like a seasoned PM who happens to have read every 10-K and \
knows every basis point."""


def build_snapshot(engine: sqlalchemy.engine.Engine) -> dict:
    """Build ~19KB JSON snapshot of system state. Cached for 60s in session_state."""
    now = time.time()
    cached = st.session_state.get("_snapshot")
    if cached and (now - st.session_state.get("_snapshot_ts", 0)) < _SNAPSHOT_TTL:
        return cached

    snap = _fetch_snapshot(engine)
    st.session_state["_snapshot"]    = snap
    st.session_state["_snapshot_ts"] = now
    return snap


def _fetch_snapshot(engine: sqlalchemy.engine.Engine) -> dict:
    today     = date.today().isoformat()
    d7        = (date.today() - timedelta(days=7)).isoformat()
    d30       = (date.today() - timedelta(days=30)).isoformat()
    next7     = (date.today() + timedelta(days=7)).isoformat()

    with engine.connect() as conn:
        # Universe size
        universe_size = conn.execute(sa.select(sa.func.count()).select_from(sp500_universe)).scalar() or 0

        # Score candidates
        latest_score_date = conn.execute(
            sa.select(sa.func.max(factor_scores_table.c.score_date))
        ).scalar()

        long_cands  = 0
        short_cands = 0
        crowding_flags: list[str] = []
        if latest_score_date:
            rows = conn.execute(
                sa.select(
                    factor_scores_table.c.ticker,
                    factor_scores_table.c.direction,
                    factor_scores_table.c.composite_score,
                ).where(factor_scores_table.c.score_date == latest_score_date)
            ).fetchall()
            long_cands   = sum(1 for r in rows if r[1] == "LONG")
            short_cands  = sum(1 for r in rows if r[1] == "SHORT")

        # Positions
        pos_rows = conn.execute(
            sa.select(
                portfolio_positions.c.ticker,
                portfolio_positions.c.direction,
                portfolio_positions.c.weight,
                portfolio_positions.c.unrealized_pnl,
                portfolio_positions.c.sector,
                portfolio_positions.c.combined_score,
            ).order_by(portfolio_positions.c.unrealized_pnl.desc())
        ).fetchall()
        positions_count = len(pos_rows)
        top5    = [dict(r._mapping) for r in pos_rows[:5]]
        bot5    = [dict(r._mapping) for r in pos_rows[-5:] if pos_rows]

        # Insider events
        insider_events = conn.execute(
            sa.select(sa.func.count()).select_from(insider_transactions)
            .where(insider_transactions.c.date >= d30)
        ).scalar() or 0

        ceo_buys = conn.execute(
            sa.select(sa.func.count()).select_from(insider_transactions)
            .where(
                insider_transactions.c.date >= d30,
                insider_transactions.c.is_ceo_cfo == 1,
                insider_transactions.c.transaction_type == "P",
            )
        ).scalar() or 0

        cluster_buys = conn.execute(
            sa.select(sa.func.count()).select_from(insider_cluster_flags)
            .where(insider_cluster_flags.c.window_end >= d30)
        ).scalar() or 0

        # Earnings in next 7 days
        earn_rows = conn.execute(
            sa.select(earnings_calendar.c.ticker)
            .where(
                earnings_calendar.c.earnings_date >= today,
                earnings_calendar.c.earnings_date <= next7,
            )
        ).fetchall()
        earnings_7d = [r[0] for r in earn_rows]

        # VIX
        vix_row = conn.execute(
            sa.select(daily_prices.c.adj_close, daily_prices.c.date)
            .where(daily_prices.c.ticker == "^VIX")
            .order_by(daily_prices.c.date.desc())
            .limit(1)
        ).fetchone()
        vix      = round(float(vix_row[0]), 2) if vix_row else None
        vix_date = vix_row[1] if vix_row else None

        # NAV + circuit breaker state
        nav_row = conn.execute(
            sa.select(portfolio_nav).order_by(portfolio_nav.c.date.desc()).limit(1)
        ).fetchone()
        nav_usd   = float(nav_row.nav)        if nav_row else 0.0
        drawdown  = float(nav_row.drawdown_pct) if nav_row else 0.0

        # Today's P&L
        attr_row = conn.execute(
            sa.select(pnl_attribution)
            .where(pnl_attribution.c.date == today)
        ).fetchone()
        pnl_today = dict(attr_row._mapping) if attr_row else {}

        # Data freshness
        price_date = conn.execute(
            sa.select(sa.func.max(daily_prices.c.date))
        ).scalar()
        score_date_val = latest_score_date

    # Halt lock
    from pathlib import Path
    halt_active = (Path("cache") / "halt.lock").exists()

    # Gross / net exposure
    total_abs_w = sum(abs(r[2]) for r in pos_rows)
    net_exp     = sum(r[2] if r[1] == "LONG" else -r[2] for r in pos_rows)

    # VIX regime
    if vix is None:
        regime = "UNKNOWN"
    elif vix < 15:
        regime = "LOW"
    elif vix < 25:
        regime = "CAUTION"
    else:
        regime = "STRESS"

    nav_prev = None
    with engine.connect() as conn:
        prev_nav = conn.execute(
            sa.select(portfolio_nav.c.nav)
            .order_by(portfolio_nav.c.date.desc())
            .limit(2)
        ).fetchall()
    if len(prev_nav) >= 2:
        nav_prev = float(prev_nav[1][0])
    nav_change_1d = ((nav_usd - nav_prev) / nav_prev) if nav_prev else 0.0

    return {
        "nav_usd":             nav_usd,
        "nav_change_1d":       round(nav_change_1d, 6),
        "gross_exposure":      round(total_abs_w, 4),
        "net_exposure":        round(net_exp, 4),
        "long_count":          sum(1 for r in pos_rows if r[1] == "LONG"),
        "short_count":         sum(1 for r in pos_rows if r[1] == "SHORT"),
        "long_candidates":     long_cands,
        "short_candidates":    short_cands,
        "universe_size":       universe_size,
        "positions_count":     positions_count,
        "crowding_flags":      crowding_flags,
        "insider_events_30d":  insider_events,
        "ceo_buys_30d":        ceo_buys,
        "cluster_buys_active": cluster_buys,
        "earnings_7d":         earnings_7d,
        "vix":                 vix,
        "vix_date":            vix_date,
        "vix_regime":          regime,
        "top5_longs":          top5,
        "worst5":              bot5,
        "pnl_today":           pnl_today,
        "drawdown_pct":        round(drawdown, 4),
        "halt_lock_active":    halt_active,
        "data_freshness": {
            "prices": price_date,
            "scores": score_date_val,
        },
    }


def render_chat(engine: sqlalchemy.engine.Engine, snapshot: dict) -> None:
    """Render the Ask JARVIS chat widget (last 6 turns, OpenAI streaming)."""
    if "jarvis_history" not in st.session_state:
        st.session_state.jarvis_history = []

    section_header = st.empty()

    # Display existing turns
    for msg in st.session_state.jarvis_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    prompt = st.chat_input("Ask JARVIS anything about the portfolio…")
    if not prompt:
        return

    st.session_state.jarvis_history.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    system_content = (
        _JARVIS_SYSTEM
        + "\n\nCurrent system snapshot:\n"
        + json.dumps(snapshot, default=str, indent=2)
    )

    messages = [{"role": "system", "content": system_content}]
    for turn in st.session_state.jarvis_history[-_CHAT_MAX_TURNS:]:
        messages.append(turn)

    try:
        import openai
        client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])

        with st.chat_message("assistant"):
            response_placeholder = st.empty()
            full_response = ""
            stream = client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                stream=True,
                max_tokens=600,
                temperature=0.7,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content or ""
                full_response += delta
                response_placeholder.markdown(full_response + "▌")
            response_placeholder.markdown(full_response)

        st.session_state.jarvis_history.append({"role": "assistant", "content": full_response})

        # Keep only last 6 turns
        if len(st.session_state.jarvis_history) > _CHAT_MAX_TURNS * 2:
            st.session_state.jarvis_history = st.session_state.jarvis_history[-_CHAT_MAX_TURNS * 2:]

    except Exception as exc:
        st.error(f"JARVIS offline: {exc}")
        log.error("OpenAI chat error: %s", exc)
