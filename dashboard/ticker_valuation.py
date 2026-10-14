"""Ticker page: valuation from the latest price and quarterly fundamentals."""

from __future__ import annotations

import math
from datetime import date

import sqlalchemy as sa
import streamlit as st

from dashboard.theme import metric_card, section_header
from data.db import daily_prices, fundamentals

_TTM_FIELDS = ("net_income", "ebit", "fcf", "dividends_paid")
_MAX_TTM_SPAN_DAYS = 300  # four contiguous quarter ends are ~9 months apart; a gap makes ~12


def _num(value) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return float(value)


def _ttm(quarters: list[dict], field: str) -> float | None:
    """Sum of the four latest quarters, if they form a trailing year and all are present."""
    if len(quarters) < 4:
        return None
    first, last = (date.fromisoformat(q["period_end"][:10]) for q in (quarters[3], quarters[0]))
    if (last - first).days > _MAX_TTM_SPAN_DAYS:
        return None
    values = [_num(q.get(field)) for q in quarters[:4]]
    if any(v is None for v in values):
        return None
    # Dividends are cash outflows; sum their sizes so mixed signs can't cancel
    return sum(abs(v) for v in values) if field == "dividends_paid" else sum(values)


def _ratio(numerator: float | None, denominator: float | None):
    """Multiple, or "n/m" when either side is zero or negative."""
    if numerator is None or denominator is None:
        return None
    return "n/m" if denominator <= 0 or numerator <= 0 else numerator / denominator


def valuation(close: float | None, quarters: list[dict]) -> dict | None:
    """Valuation metrics; quarters newest first. None without a price and share count."""
    # Balance-sheet items from the latest quarter that has them (Yahoo can publish the income
    # statement before the balance sheet, leaving the newest row's balance fields empty)
    latest = next((q for q in quarters if _num(q.get("shares_outstanding"))), {})
    shares = _num(latest.get("shares_outstanding"))
    close = _num(close)
    if not close or not shares:
        return None
    mcap = close * shares
    debt, cash = _num(latest.get("total_debt")), _num(latest.get("cash"))
    ev = mcap + debt - cash if debt is not None and cash is not None else None
    ttm = {f: _ttm(quarters, f) for f in _TTM_FIELDS}
    fcf, divs = ttm["fcf"], ttm["dividends_paid"]
    return {
        "market_cap": mcap,
        "ev": ev,
        "pe": _ratio(mcap, ttm["net_income"]),
        "ev_ebit": _ratio(ev, ttm["ebit"]),
        "pb": _ratio(mcap, _num(latest.get("total_equity"))),
        "fcf_yield": fcf / mcap if fcf is not None else None,
        "div_yield": divs / mcap if divs is not None else None,
        "quarter_end": latest.get("period_end"),
    }


_UNITS = ((1e6, "M", 0), (1e9, "B", 1), (1e12, "T", 2))


def fmt_money(value: float | None) -> str:
    if value is None:
        return "—"
    sign, size = ("-" if value < 0 else ""), abs(value)
    unit = next((i for i in range(len(_UNITS) - 1, -1, -1) if size >= _UNITS[i][0]), None)
    # Step up a unit when rounding reaches 1000 (999.96B shows as $1.00T, not $1000.0B)
    if unit is None and round(size) >= 1e6:
        unit = 0
    elif unit is not None and unit + 1 < len(_UNITS):
        scale, _, places = _UNITS[unit]
        if round(size / scale, places) >= 1000:
            unit += 1
    if unit is None:
        return f"{sign}${size:,.0f}"
    scale, suffix, places = _UNITS[unit]
    return f"{sign}${size / scale:.{places}f}{suffix}"


def fmt_multiple(value) -> str:
    if value is None:
        return "—"
    return value if isinstance(value, str) else f"{value:.1f}x"


def fmt_pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def render_valuation(engine, ticker: str) -> None:
    section_header("VALUATION")
    with engine.connect() as conn:
        price = conn.execute(
            sa.select(daily_prices.c.date, daily_prices.c.close)
            .where(daily_prices.c.ticker == ticker)
            .order_by(daily_prices.c.date.desc())
            .limit(1)
        ).fetchone()
        quarters = [
            r._asdict()
            for r in conn.execute(
                sa.select(fundamentals)
                .where(
                    (fundamentals.c.ticker == ticker) & (fundamentals.c.period_type == "quarterly")
                )
                .order_by(fundamentals.c.period_end.desc())
                .limit(6)
            )
        ]
    v = valuation(price.close if price else None, quarters)
    if v is None:
        st.caption("No valuation data.")
        return
    st.caption(
        f"Close on {price.date}; balance sheet at the quarter ending {v['quarter_end']}; "
        "P/E, EV/EBIT and yields over the last four quarters."
    )
    cards = [
        ("Market Cap", fmt_money(v["market_cap"])),
        ("Enterprise Value", fmt_money(v["ev"])),
        ("P/E (TTM)", fmt_multiple(v["pe"])),
        ("EV/EBIT (TTM)", fmt_multiple(v["ev_ebit"])),
        ("Price / Book", fmt_multiple(v["pb"])),
        ("FCF Yield", fmt_pct(v["fcf_yield"])),
        ("Dividend Yield", fmt_pct(v["div_yield"])),
    ]
    for col, (label, text) in zip(st.columns(len(cards)), cards, strict=True):
        with col:
            metric_card(label, text)
