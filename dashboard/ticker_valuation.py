"""Ticker page: valuation from the latest price and quarterly fundamentals."""

from __future__ import annotations

from datetime import date, timedelta

import sqlalchemy as sa
import streamlit as st

from dashboard.theme import metric_card, section_header
from data.db import daily_prices, fundamentals, market_caps
from factors.loader import MCAP_LOOKBACK_DAYS
from factors.market_cap import shares_from_snapshots
from factors.ttm import num as _num
from factors.ttm import ttm as _ttm

_TTM_FIELDS = ("net_income", "operating_income", "fcf", "dividends_paid")


def _ratio(numerator: float | None, denominator: float | None):
    """Multiple, or "n/m" when either side is zero or negative."""
    if numerator is None or denominator is None:
        return None
    return "n/m" if denominator <= 0 or numerator <= 0 else numerator / denominator


def valuation(
    close: float | None, quarters: list[dict], shares: float | None = None
) -> dict | None:
    """Valuation metrics; quarters newest first. None without a price and share count.

    shares: effective shares from the market cap snapshots; without them, the balance sheet's.
    """
    # Balance-sheet items from the latest quarter that has them (Yahoo can publish the income
    # statement before the balance sheet, leaving the newest row's balance fields empty)
    latest = next((q for q in quarters if _num(q.get("shares_outstanding"))), {})
    shares = shares or _num(latest.get("shares_outstanding"))
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
        "ev_ebit": _ratio(ev, ttm["operating_income"]),
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
                .limit(8)
            )
        ]
        shares = _snapshot_shares(conn, ticker, price.date if price else None)
    v = valuation(price.close if price else None, quarters, shares)
    if v is None:
        st.caption("No valuation data.")
        return
    st.caption(
        f"Close on {price.date}; balance sheet at the quarter ending {v['quarter_end']}; "
        "P/E, EV/EBIT and yields over the last four reported quarters."
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


def _snapshot_shares(conn, ticker: str, as_of: str | None) -> float | None:
    """Effective shares from the ticker's stored market caps (the scoring helper's rule and
    window, ending at the latest close)."""
    if as_of is None:
        return None
    start = (date.fromisoformat(as_of) - timedelta(days=MCAP_LOOKBACK_DAYS)).isoformat()
    snaps = conn.execute(
        sa.select(market_caps.c.date, market_caps.c.market_cap)
        .where(
            (market_caps.c.ticker == ticker)
            & (market_caps.c.date >= start)
            & (market_caps.c.date <= as_of)
        )
        .order_by(market_caps.c.date.desc())
        .limit(5)
    ).fetchall()
    paired = []
    for day, mcap in snaps:
        close = conn.execute(
            sa.select(daily_prices.c.close)
            .where((daily_prices.c.ticker == ticker) & (daily_prices.c.date <= day))
            .order_by(daily_prices.c.date.desc())
            .limit(1)
        ).scalar()
        paired.append((_num(mcap), _num(close)))
    return shares_from_snapshots(paired)
