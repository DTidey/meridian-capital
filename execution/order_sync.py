"""Bring open execution_orders rows up to date with Alpaca's view of each order."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import sqlalchemy as sa

from execution.costs import compute_slippage
from execution.db import execution_orders

log = logging.getLogger(__name__)

OPEN_STATUSES = ("PENDING", "PARTIAL")
_CANCELLED = {"canceled", "expired", "replaced"}
_FAILED = {"rejected"}  # suspended is not final: Alpaca may still release the order


def db_status(alpaca_status, filled_qty: float) -> str:
    """Map an Alpaca order status to PENDING / PARTIAL / FILLED / CANCELLED / FAILED."""
    status = str(getattr(alpaca_status, "value", alpaca_status)).lower()
    if status == "filled":
        return "FILLED"
    if status in _FAILED:
        return "FAILED"
    if status in _CANCELLED:
        return "CANCELLED"  # filled_shares keeps any partial fill
    return "PARTIAL" if filled_qty > 0 else "PENDING"


def sync_orders(conn, client) -> dict:
    """Update every open row from Alpaca; final rows are never fetched again."""
    rows = conn.execute(
        sa.select(execution_orders).where(
            execution_orders.c.status.in_(OPEN_STATUSES) & execution_orders.c.order_id.isnot(None)
        )
    ).fetchall()
    updated = final = 0
    for row in rows:
        try:
            values = _order_values(client.get_order_by_id(row.order_id), row.action)
        except Exception as exc:
            log.warning("Order sync: could not read %s (%s): %s", row.order_id, row.ticker, exc)
            continue
        if values["status"] != row.status or values["filled_shares"] != (row.filled_shares or 0):
            conn.execute(
                execution_orders.update().where(execution_orders.c.id == row.id).values(**values)
            )
            updated += 1
        if values["status"] not in OPEN_STATUSES:
            final += 1
    conn.commit()
    summary = {"checked": len(rows), "updated": updated, "final": final}
    log.info("Order sync: %s", summary)
    return summary


def _order_values(order, action: str) -> dict:
    filled = float(order.filled_qty or 0)
    fill_price = float(order.filled_avg_price) if order.filled_avg_price else None
    limit = float(order.limit_price) if order.limit_price else None
    slippage = compute_slippage(limit, fill_price, action) if fill_price and limit else None
    return {
        "status": db_status(order.status, filled),
        "filled_shares": filled,
        "avg_fill_price": fill_price,
        "slippage_bps": slippage,
        "updated_at": datetime.now(UTC).isoformat(),
    }
