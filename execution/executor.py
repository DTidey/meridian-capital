"""Submit and record Alpaca orders for approved position changes (order_sync.py tracks them)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa

from data.db import daily_prices
from execution.broker import get_broker_positions
from execution.db import execution_orders
from execution.order_sync import db_status
from execution.short_check import is_shortable
from execution.symbols import from_alpaca, to_alpaca
from portfolio.db import portfolio_positions, position_approvals

log = logging.getLogger(__name__)

_CLOSING_ACTIONS = {"SELL", "COVER"}
_OPEN_ORDERS_LIMIT = 500  # Alpaca maximum per listing


# ---------------------------------------------------------------------------
# Pricing helpers
# ---------------------------------------------------------------------------


def _limit_price(action: str, current_price: float, slippage_pct: float = 0.005) -> float:
    if action in ("BUY", "COVER"):
        return round(current_price * (1.0 + slippage_pct), 2)
    return round(current_price * (1.0 - slippage_pct), 2)


def _chunk_orders(shares: float, adv: float | None, max_adv_pct: float = 0.02) -> list[float]:
    """Split shares into chunks of at most max_adv_pct × ADV. Returns list of abs chunk sizes."""
    if adv is None or adv <= 0:
        return [abs(shares)]
    chunk_size = max_adv_pct * adv
    if chunk_size <= 0:
        return [abs(shares)]
    remaining = abs(shares)
    chunks: list[float] = []
    while remaining > 0:
        chunks.append(min(remaining, chunk_size))
        remaining -= chunk_size
    return chunks


# ---------------------------------------------------------------------------
# Order submission
# ---------------------------------------------------------------------------


def submit_order(
    client,
    ticker: str,
    action: str,
    shares: float,
    current_price: float,
    config: dict,
    dry_run: bool = False,
) -> object | None:
    """
    Submit a limit order to Alpaca.
    Returns the Alpaca order, or None on dry_run / failure.
    time_in_force: day (submitted after the close, Alpaca queues it for the next session).
    """
    from alpaca.trading.enums import OrderSide, PositionIntent, TimeInForce
    from alpaca.trading.requests import LimitOrderRequest

    slippage_pct = config.get("execution", {}).get("limit_slippage_pct", 0.005)
    limit = _limit_price(action, current_price, slippage_pct)

    side_map = {
        "BUY": OrderSide.BUY,
        "COVER": OrderSide.BUY,
        "SELL": OrderSide.SELL,
        "SHORT": OrderSide.SELL,
    }
    intent_map = {
        "BUY": PositionIntent.BUY_TO_OPEN,
        "COVER": PositionIntent.BUY_TO_CLOSE,
        "SELL": PositionIntent.SELL_TO_CLOSE,
        "SHORT": PositionIntent.SELL_TO_OPEN,
    }

    side = side_map[action]
    intent = intent_map[action]
    qty = int(round(abs(shares)))

    if dry_run:
        log.info(
            "[DRY-RUN] %s %d %s @ limit %.2f (day)",
            action,
            qty,
            ticker,
            limit,
        )
        return None

    req = LimitOrderRequest(
        symbol=to_alpaca(ticker),
        qty=qty,
        side=side,
        time_in_force=TimeInForce.DAY,
        limit_price=limit,
        position_intent=intent,
    )
    try:
        order = client.submit_order(req)
        log.info(
            "Submitted %s order for %s: qty=%d limit=%.2f id=%s",
            action,
            ticker,
            qty,
            limit,
            order.id,
        )
        return order
    except Exception as exc:
        log.error("Failed to submit %s order for %s: %s", action, ticker, exc)
        return None


# ---------------------------------------------------------------------------
# Main execution loop
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).isoformat()


def execute_approvals(
    conn,
    client,
    score_date: str,
    config: dict,
    cache_dir: Path,
    dry_run: bool = False,
) -> list[dict]:
    """
    Submit DAY limit orders for all APPROVED position_approvals for score_date.

    Orders are not polled: after the close Alpaca queues them for the next session, and the
    next run's order sync (execution/order_sync.py) records fills and final status.
    Returns list of result dicts (one per approval).
    """
    approvals = conn.execute(
        sa.select(position_approvals).where(
            sa.and_(
                position_approvals.c.rebalance_date == score_date,
                position_approvals.c.status == "APPROVED",
            )
        )
    ).fetchall()

    if not approvals:
        log.info("No APPROVED position_approvals for %s.", score_date)
        return []

    # None means the fetch failed (skip the closing-order guard).
    try:
        alpaca_positions: dict | None = get_broker_positions(client)
    except Exception as exc:
        log.warning("Could not fetch Alpaca positions — skipping drift check: %s", exc)
        alpaca_positions = None
    # Raises if open orders cannot be listed: never risk a duplicate order.
    open_tickers = set() if dry_run else _open_order_tickers(client)

    tickers = [a.ticker for a in approvals]
    ctx = {
        "closes": _closes_on(conn, tickers, score_date),
        "cur_shares": _current_shares(conn),
        "alpaca_positions": alpaca_positions,
        "open_tickers": open_tickers,
        "cache_dir": cache_dir,
    }
    return [
        _execute_one(conn, client, appr, score_date, config, ctx, dry_run) for appr in approvals
    ]


def _execute_one(conn, client, appr, score_date, config, ctx, dry_run) -> dict:
    ex_cfg = config.get("execution", {})
    ticker, action = appr.ticker, appr.action
    target_shares = float(appr.target_shares or 0)
    delta_shares = (
        float(appr.delta_shares)
        if appr.delta_shares is not None
        else target_shares - ctx["cur_shares"].get(ticker, 0)
    )
    reason = _direction_skip_reason(ticker, action, delta_shares) or _skip_reason(
        client, ticker, action, ctx, ex_cfg
    )
    if reason:
        log.warning("%s %s skipped: %s", action, ticker, reason)
        return {"ticker": ticker, "action": action, "status": "SKIPPED", "reason": reason}

    price = ctx["closes"][ticker]
    chunks = _chunk_orders(delta_shares, None, ex_cfg.get("max_adv_pct", 0.02))
    statuses = [
        _submit_chunk(conn, client, score_date, ticker, action, chunk, price, config, dry_run)
        for chunk in chunks
    ]
    if dry_run:
        status = "DRY_RUN"
    elif "FAILED" in statuses:
        status = "FAILED"
    else:
        status = "SUBMITTED"
    return {
        "ticker": ticker,
        "action": action,
        "ordered_shares": abs(delta_shares),
        "filled_shares": 0.0,
        "status": status,
    }


def _direction_skip_reason(ticker: str, action: str, delta_shares: float) -> str | None:
    """Refuse no-op rows and rows whose action disagrees with the delta sign."""
    if action == "HOLD" or round(abs(delta_shares)) == 0:
        return "no_change"
    if (action in ("BUY", "COVER")) != (delta_shares > 0):
        log.error("%s %s has delta %+.2f: action and delta disagree", action, ticker, delta_shares)
        return "action_mismatch"
    return None


def _skip_reason(client, ticker: str, action: str, ctx: dict, ex_cfg: dict) -> str | None:
    if ticker not in ctx["closes"]:
        return "no_close"
    if ticker in ctx["open_tickers"]:
        return "open_order"
    positions = ctx["alpaca_positions"]
    if action in _CLOSING_ACTIONS and positions is not None:
        broker_qty = positions.get(ticker, 0.0)
        if (action == "SELL" and broker_qty <= 0) or (action == "COVER" and broker_qty >= 0):
            return "no_alpaca_position"  # the next book sync corrects the local book
    shortable_days = ex_cfg.get("shortable_cache_days", 7)
    if action == "SHORT" and not is_shortable(ticker, client, ctx["cache_dir"], shortable_days):
        return "not_shortable"
    return None


def _submit_chunk(conn, client, score_date, ticker, action, chunk, price, config, dry_run):
    """Insert the execution_orders row, submit, and record Alpaca's answer; return the status."""
    now_str = _now()
    row_id = conn.execute(
        execution_orders.insert().values(
            rebalance_date=score_date,
            ticker=ticker,
            action=action,
            ordered_shares=chunk,
            filled_shares=0.0,
            status="PENDING",
            created_at=now_str,
            updated_at=now_str,
        )
    ).inserted_primary_key[0]
    conn.commit()

    order = submit_order(client, ticker, action, chunk, price, config, dry_run=dry_run)
    if dry_run:
        values = {"status": "DRY_RUN", "order_id": "dry-run"}
    elif order is None:
        values = {"status": "FAILED"}
    else:
        filled = float(order.filled_qty or 0)
        values = {"status": db_status(order.status, filled), "order_id": str(order.id)}
    conn.execute(
        execution_orders.update()
        .where(execution_orders.c.id == row_id)
        .values(**values, updated_at=_now())
    )
    conn.commit()
    return values["status"]


def _open_order_tickers(client) -> set[str]:
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    orders = client.get_orders(
        GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=_OPEN_ORDERS_LIMIT)
    )
    if len(orders) >= _OPEN_ORDERS_LIMIT:
        # Alpaca caps one listing; a truncated list cannot rule out duplicates.
        raise RuntimeError(f"{len(orders)} open orders: listing may be truncated")
    return {from_alpaca(o.symbol) for o in orders}


def _closes_on(conn, tickers: list[str], score_date: str) -> dict[str, float]:
    """Stored close for score_date: the session that just ended."""
    rows = conn.execute(
        sa.select(daily_prices.c.ticker, daily_prices.c.close).where(
            daily_prices.c.ticker.in_(tickers)
            & (daily_prices.c.date == score_date)
            & (daily_prices.c.close > 0)
        )
    ).fetchall()
    return {t: float(c) for t, c in rows}


def _current_shares(conn) -> dict[str, float]:
    rows = conn.execute(
        sa.select(portfolio_positions.c.ticker, portfolio_positions.c.shares)
    ).fetchall()
    return {t: float(s or 0) for t, s in rows}
