"""Slippage computation and 30-day aggregation stats."""

from __future__ import annotations

import logging
from datetime import date, timedelta

import sqlalchemy as sa

from execution.db import execution_orders

log = logging.getLogger(__name__)


def compute_slippage(ordered_price: float, filled_price: float, side: str) -> float:
    """
    Return signed slippage in basis points.
    Positive = adverse (paid more / received less than expected).
    side: 'buy' | 'sell' | 'short' | 'cover'
    """
    if ordered_price <= 0:
        return 0.0
    diff = filled_price - ordered_price
    # For buy/cover: paying more is adverse (+). For sell/short: receiving less is adverse (+).
    if side.lower() in ("buy", "cover"):
        signed = diff / ordered_price
    else:
        signed = -diff / ordered_price
    return signed * 10_000.0


def slippage_stats(conn, days: int = 30) -> dict:
    """Return aggregate slippage stats for FILLED orders in the last *days* days."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()

    rows = conn.execute(
        sa.select(
            execution_orders.c.ticker,
            execution_orders.c.slippage_bps,
        ).where(
            sa.and_(
                execution_orders.c.status == "FILLED",
                execution_orders.c.created_at >= cutoff,
                execution_orders.c.slippage_bps.isnot(None),
            )
        )
    ).fetchall()

    if not rows:
        return {"mean_bps": 0.0, "p95_bps": 0.0, "worst_ticker": None, "count": 0}

    tickers = [r[0] for r in rows]
    bps = [r[1] for r in rows]

    sorted_bps = sorted(bps)
    n = len(sorted_bps)
    p95_idx = max(0, int(n * 0.95) - 1)
    worst_idx = bps.index(max(bps))

    return {
        "mean_bps": sum(bps) / n,
        "p95_bps": sorted_bps[p95_idx],
        "worst_ticker": tickers[worst_idx],
        "count": n,
    }
