"""Daily LP letter — generated via OpenAI, cached in lp_letters table."""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING

import sqlalchemy as sa

from data.db import daily_prices, insert_or_replace
from portfolio.db import portfolio_positions
from reporting.db import lp_letters, pnl_attribution, portfolio_nav
from risk.db import risk_events

if TYPE_CHECKING:
    import sqlalchemy.engine

log = logging.getLogger(__name__)

_JARVIS_SYSTEM = """You are JARVIS — the portfolio intelligence system for Meridian Capital Partners, \
a quantitative long/short equity hedge fund. You speak with authority, precision, and a dry wit. \
You reference specific positions, factor scores, and risk metrics. You never hedge with "I think" \
or "perhaps". You write like a seasoned PM who happens to have read every 10-K and \
knows every basis point."""


def generate(
    engine: sqlalchemy.engine.Engine,
    cfg: dict | None = None,
    force: bool = False,
    letter_date: str | None = None,
) -> str:
    """Generate (or return cached) daily LP letter body text.

    Returns the body-only content (no letterhead).
    """
    cfg         = cfg or {}
    today_str   = letter_date or date.today().isoformat()

    with engine.connect() as conn:
        cached = conn.execute(
            sa.select(lp_letters.c.content)
            .where(lp_letters.c.letter_date == today_str)
        ).fetchone()

    if cached and not force:
        return cached[0]

    context = _build_context(engine)
    content = _call_openai(context, cfg)
    doc_id  = f"MCP-IM-{today_str[:4]}-{today_str[5:7]}{today_str[8:]}"

    with engine.begin() as conn:
        ins = insert_or_replace(conn, lp_letters)
        conn.execute(ins, [{
            "letter_date":  today_str,
            "doc_id":       doc_id,
            "content":      content,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }])

    log.info("LP letter generated for %s", today_str)
    return content


def render_full(letter_date: str, content: str, nav_usd: float = 0.0, inception_date: str = "2024-01-02") -> str:
    """Render full letter including letterhead, body, signature, and compliance footer."""
    d        = datetime.strptime(letter_date, "%Y-%m-%d")
    doc_id   = f"MCP-IM-{letter_date[:4]}-{letter_date[5:7]}{letter_date[8:]}"
    date_str = d.strftime("%d %B %Y")

    return f"""---

# MERIDIAN CAPITAL PARTNERS

**Wilmington, Delaware** · **Inception:** {inception_date} · **AUM:** ${nav_usd:,.0f}

**Doc:** {doc_id} &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; **{date_str}**

---

> **CONFIDENTIAL · LIMITED PARTNERS ONLY**

---

Dear Limited Partners,

{content}

---

Respectfully,

**JARVIS**
Portfolio Intelligence System
Meridian Capital Partners

---

*This communication is confidential and intended solely for the named recipients. Nothing herein \
constitutes investment advice or a solicitation. Past performance is not indicative of future \
results. Investing in a hedge fund involves material risks, including possible loss of principal. \
This document is for informational purposes only and is subject to change without notice. For \
accredited investors only.*
"""


def _build_context(engine: sqlalchemy.engine.Engine) -> str:
    today = date.today().isoformat()
    cutoff_7d = (date.today() - timedelta(days=7)).isoformat()

    with engine.connect() as conn:
        attr_today = conn.execute(
            sa.select(pnl_attribution)
            .where(pnl_attribution.c.date == today)
        ).fetchone()

        nav_row = conn.execute(
            sa.select(portfolio_nav).order_by(portfolio_nav.c.date.desc()).limit(1)
        ).fetchone()

        risk_today = conn.execute(
            sa.select(risk_events)
            .where(risk_events.c.event_date == today)
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
                portfolio_positions.c.sector,
            ).order_by(portfolio_positions.c.unrealized_pnl.desc())
        ).fetchall()

        longs  = [r for r in pos_rows if r[1] == "LONG"]
        shorts = [r for r in pos_rows if r[1] == "SHORT"]

    nav = dict(nav_row._mapping) if nav_row else {}

    ctx = {
        "date":          today,
        "pnl_today":     dict(attr_today._mapping) if attr_today else {},
        "current_nav":   nav,
        "gross_exposure": sum(abs(r[3]) for r in pos_rows),
        "net_exposure":   sum(r[3] if r[1] == "LONG" else -r[3] for r in pos_rows),
        "long_count":    len(longs),
        "short_count":   len(shorts),
        "top3_movers":   [dict(r._mapping) for r in pos_rows[:3]],
        "vix":           round(float(vix_row[1]), 2) if vix_row else None,
        "risk_events":   [dict(r._mapping) for r in risk_today],
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
                "Write the daily LP letter body for Meridian Capital Partners. "
                "3-4 paragraphs. Reference today's P&L attribution, key position moves, "
                "and any risk events. Tone: institutional, precise, dry wit. "
                "Do not include salutation or signature — body only.\n\n"
                f"Context:\n{context}"
            )},
        ],
        max_tokens=600,
        temperature=0.7,
    )
    return resp.choices[0].message.content.strip()
