"""JARVIS weekly commentary — generated via OpenAI, cached in weekly_commentary table."""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING

import sqlalchemy as sa

from data.db import daily_prices, insert_or_replace
from portfolio.db import portfolio_positions
from reporting.db import pnl_attribution, portfolio_nav, weekly_commentary
from risk.db import risk_events

if TYPE_CHECKING:
    import sqlalchemy.engine

log = logging.getLogger(__name__)

_JARVIS_SYSTEM = """You are JARVIS — the portfolio intelligence system for Meridian Capital Partners, \
a quantitative long/short equity hedge fund. You speak with authority, precision, and a dry wit. \
You reference specific positions, factor scores, and risk metrics. You never hedge with "I think" \
or "perhaps". You write like a seasoned PM who happens to have read every 10-K and \
knows every basis point."""


def generate_if_due(
    engine: sqlalchemy.engine.Engine,
    cfg: dict | None = None,
    force: bool = False,
) -> str | None:
    """Generate weekly commentary if today is the configured weekday and not already cached.

    Returns commentary text or None if not due and not forced.
    """
    cfg = cfg or {}
    weekday = int((cfg.get("reporting") or {}).get("commentary_weekday", 4))
    today   = date.today()

    if today.weekday() != weekday and not force:
        log.debug("Commentary not due today (weekday=%d, target=%d)", today.weekday(), weekday)
        return None

    # Monday of this week
    week_start = (today - timedelta(days=today.weekday())).isoformat()

    with engine.connect() as conn:
        cached = conn.execute(
            sa.select(weekly_commentary.c.content)
            .where(weekly_commentary.c.week_start == week_start)
        ).fetchone()

    if cached and not force:
        log.debug("Commentary already cached for week_start=%s", week_start)
        return cached[0]

    context = _build_context(engine)
    content = _call_openai(context, cfg)

    with engine.begin() as conn:
        ins = insert_or_replace(conn, weekly_commentary)
        conn.execute(ins, [{
            "week_start":   week_start,
            "content":      content,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }])

    log.info("Weekly commentary generated for week_start=%s", week_start)
    return content


def _build_context(engine: sqlalchemy.engine.Engine) -> str:
    cutoff_5d = (date.today() - timedelta(days=5)).isoformat()
    cutoff_7d = (date.today() - timedelta(days=7)).isoformat()

    with engine.connect() as conn:
        attr_rows = conn.execute(
            sa.select(pnl_attribution)
            .where(pnl_attribution.c.date >= cutoff_5d)
            .order_by(pnl_attribution.c.date)
        ).fetchall()

        nav_row = conn.execute(
            sa.select(portfolio_nav).order_by(portfolio_nav.c.date.desc()).limit(1)
        ).fetchone()

        risk_rows = conn.execute(
            sa.select(risk_events)
            .where(risk_events.c.event_date >= cutoff_7d)
            .order_by(risk_events.c.event_date)
        ).fetchall()

        vix_row = conn.execute(
            sa.select(daily_prices.c.date, daily_prices.c.adj_close)
            .where(daily_prices.c.ticker == "^VIX")
            .order_by(daily_prices.c.date.desc())
            .limit(1)
        ).fetchone()

        pos_rows = conn.execute(
            sa.select(
                portfolio_positions.c.ticker,
                portfolio_positions.c.direction,
                portfolio_positions.c.unrealized_pnl,
                portfolio_positions.c.weight,
            ).order_by(portfolio_positions.c.unrealized_pnl.desc())
        ).fetchall()

    attr_data = [dict(r._mapping) for r in attr_rows]
    nav_data  = dict(nav_row._mapping) if nav_row else {}
    risk_data = [dict(r._mapping) for r in risk_rows]
    vix       = round(float(vix_row[1]), 2) if vix_row else None
    top5      = [dict(r._mapping) for r in pos_rows[:5]]
    bot5      = [dict(r._mapping) for r in pos_rows[-5:]]

    ctx = {
        "period":         f"Week of {date.today().isoformat()}",
        "pnl_5d":         attr_data,
        "current_nav":    nav_data,
        "vix":            vix,
        "risk_events":    risk_data,
        "top5_positions": top5,
        "worst5_positions": bot5,
    }
    return json.dumps(ctx, default=str, indent=2)


def _call_openai(context: str, cfg: dict) -> str:
    import openai
    model  = (cfg.get("analysis") or {}).get("openai_model", "gpt-4o")
    client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    resp   = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _JARVIS_SYSTEM},
            {"role": "user",   "content": (
                "Write the weekly portfolio commentary for Meridian Capital Partners. "
                "Reference specific P&L drivers, risk events, and position performance. "
                "Tone: authoritative, precise, wry. 300-400 words.\n\n"
                f"Context:\n{context}"
            )},
        ],
        max_tokens=800,
        temperature=0.7,
    )
    return resp.choices[0].message.content.strip()
