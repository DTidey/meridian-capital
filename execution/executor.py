"""Submit, poll, and record Alpaca orders for approved position changes."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa

from data.db import daily_prices
from execution.costs import compute_slippage
from execution.db import execution_orders
from execution.short_check import is_shortable
from portfolio.db import portfolio_positions, position_approvals

log = logging.getLogger(__name__)

_CLOSING_ACTIONS = {"SELL", "COVER"}


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
) -> str | None:
    """
    Submit a limit order to Alpaca.
    Returns Alpaca order UUID or None on dry_run / failure.
    time_in_force: day (expires at market close).
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
        symbol=ticker,
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
        return str(order.id)
    except Exception as exc:
        log.error("Failed to submit %s order for %s: %s", action, ticker, exc)
        return None


# ---------------------------------------------------------------------------
# Order polling
# ---------------------------------------------------------------------------


def poll_order(client, order_id: str, timeout_s: int = 120, interval_s: int = 5) -> dict:
    """
    Poll until the order reaches a terminal state or timeout.
    Returns dict with status, filled_qty, avg_fill_price.
    """
    terminal = {"filled", "cancelled", "expired", "rejected", "done_for_day"}
    deadline = time.monotonic() + timeout_s

    while time.monotonic() < deadline:
        try:
            order = client.get_order_by_id(order_id)
            status = str(order.status).lower()
            if status in terminal:
                return {
                    "status": status,
                    "filled_qty": float(order.filled_qty or 0),
                    "avg_fill_price": float(order.filled_avg_price or 0) or None,
                }
        except Exception as exc:
            log.warning("Error polling order %s: %s", order_id, exc)

        time.sleep(interval_s)

    # Timeout — return last known state (treat as partial/cancelled)
    log.warning("Order %s timed out after %ds — marking as CANCELLED.", order_id, timeout_s)
    return {"status": "cancelled", "filled_qty": 0.0, "avg_fill_price": None}


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
    Execute all APPROVED position_approvals for score_date.
    Returns list of result dicts (one per ticker).
    """
    ex_cfg = config.get("execution", {})
    max_adv_pct = ex_cfg.get("max_adv_pct", 0.02)
    poll_timeout = ex_cfg.get("poll_timeout_s", 120)
    poll_interval = ex_cfg.get("poll_interval_s", 5)
    shortable_days = ex_cfg.get("shortable_cache_days", 7)

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

    # Fetch actual Alpaca positions to guard against local-DB / broker drift.
    # None means the fetch failed (skip drift check); {} means fetch succeeded but no positions.
    try:
        alpaca_positions: dict | None = {p.symbol: p for p in client.get_all_positions()}
    except Exception as exc:
        log.warning("Could not fetch Alpaca positions — skipping drift check: %s", exc)
        alpaca_positions = None

    # Load current prices from portfolio_positions
    price_rows = conn.execute(
        sa.select(
            portfolio_positions.c.ticker,
            portfolio_positions.c.current_price,
            portfolio_positions.c.shares,
        )
    ).fetchall()
    prices: dict[str, float] = {r[0]: float(r[1] or 0) for r in price_rows}
    cur_shares: dict[str, float] = {r[0]: float(r[2] or 0) for r in price_rows}

    # For any approved ticker missing a price, fall back to the latest daily_prices close
    missing = [a.ticker for a in approvals if prices.get(a.ticker, 0.0) <= 0]
    if missing:
        fallback_rows = conn.execute(
            sa.select(daily_prices.c.ticker, daily_prices.c.adj_close)
            .where(
                sa.and_(
                    daily_prices.c.ticker.in_(missing),
                    daily_prices.c.adj_close.isnot(None),
                )
            )
            .order_by(daily_prices.c.date.desc())
        ).fetchall()
        seen: set[str] = set()
        for ticker, price in fallback_rows:
            if ticker not in seen and price and price > 0:
                prices[ticker] = float(price)
                seen.add(ticker)

    results: list[dict] = []

    for appr in approvals:
        ticker = appr.ticker
        action = appr.action
        target_shares = float(appr.target_shares or 0)
        current_price = prices.get(ticker, 0.0)
        adv: float | None = None  # ADV not stored on approvals — chunk logic uses None

        if current_price <= 0:
            log.warning("No current price for %s — skipping.", ticker)
            continue

        # Guard: closing orders require Alpaca to actually hold the position.
        if action in _CLOSING_ACTIONS and alpaca_positions is not None:
            ap = alpaca_positions.get(ticker)
            has_long = ap is not None and float(ap.qty) > 0
            has_short = ap is not None and float(ap.qty) < 0
            if action == "SELL" and not has_long:
                log.warning(
                    "SELL skipped for %s — no long position found in Alpaca (local DB is stale).",
                    ticker,
                )
                conn.execute(
                    portfolio_positions.delete().where(portfolio_positions.c.ticker == ticker)
                )
                conn.commit()
                results.append(
                    {
                        "ticker": ticker,
                        "action": action,
                        "status": "SKIPPED",
                        "reason": "no_alpaca_position",
                    }
                )
                continue
            if action == "COVER" and not has_short:
                log.warning(
                    "COVER skipped for %s — no short position found in Alpaca (local DB is stale).",
                    ticker,
                )
                conn.execute(
                    portfolio_positions.delete().where(portfolio_positions.c.ticker == ticker)
                )
                conn.commit()
                results.append(
                    {
                        "ticker": ticker,
                        "action": action,
                        "status": "SKIPPED",
                        "reason": "no_alpaca_position",
                    }
                )
                continue

        # Shortability gate for opening short positions
        if action == "SHORT" and not is_shortable(ticker, client, cache_dir, shortable_days):
            log.warning("%s is not shortable — skipping SHORT.", ticker)
            results.append(
                {
                    "ticker": ticker,
                    "action": action,
                    "status": "SKIPPED",
                    "reason": "not_shortable",
                }
            )
            continue

        delta_shares = float(appr.delta_shares or (target_shares - cur_shares.get(ticker, 0)))
        chunks = _chunk_orders(delta_shares, adv, max_adv_pct)

        ticker_filled = 0.0
        ticker_status = "PENDING"
        fill_prices: list[float] = []

        for chunk in chunks:
            # Insert execution_orders row
            now_str = _now()
            ins_result = conn.execute(
                execution_orders.insert().values(
                    rebalance_date=score_date,
                    ticker=ticker,
                    action=action,
                    ordered_shares=chunk,
                    filled_shares=0.0,
                    avg_fill_price=None,
                    order_id=None,
                    status="PENDING",
                    slippage_bps=None,
                    created_at=now_str,
                    updated_at=now_str,
                )
            )
            conn.commit()
            row_id = ins_result.inserted_primary_key[0]

            order_id = submit_order(
                client, ticker, action, chunk, current_price, config, dry_run=dry_run
            )

            if dry_run:
                conn.execute(
                    execution_orders.update()
                    .where(execution_orders.c.id == row_id)
                    .values(status="DRY_RUN", order_id="dry-run", updated_at=_now())
                )
                conn.commit()
                continue

            if order_id is None:
                conn.execute(
                    execution_orders.update()
                    .where(execution_orders.c.id == row_id)
                    .values(status="FAILED", updated_at=_now())
                )
                conn.commit()
                ticker_status = "FAILED"
                continue

            conn.execute(
                execution_orders.update()
                .where(execution_orders.c.id == row_id)
                .values(order_id=order_id, updated_at=_now())
            )
            conn.commit()

            fill = poll_order(client, order_id, poll_timeout, poll_interval)
            filled_qty = fill["filled_qty"]
            fill_price = fill["avg_fill_price"]
            final_status = fill["status"]

            if filled_qty == 0:
                log.warning("Zero fill for %s order %s — marking CANCELLED.", ticker, order_id)
                final_status = "cancelled"

            slip_bps = None
            if fill_price and fill_price > 0:
                limit = _limit_price(action, current_price, ex_cfg.get("limit_slippage_pct", 0.005))
                slip_bps = compute_slippage(limit, fill_price, action)
                fill_prices.append(fill_price)

            mapped_status = {
                "filled": "FILLED",
                "cancelled": "CANCELLED",
                "expired": "CANCELLED",
                "done_for_day": "CANCELLED",
                "rejected": "FAILED",
                "partial": "PARTIAL",
            }.get(final_status, "PARTIAL" if filled_qty > 0 else "CANCELLED")

            conn.execute(
                execution_orders.update()
                .where(execution_orders.c.id == row_id)
                .values(
                    filled_shares=filled_qty,
                    avg_fill_price=fill_price,
                    status=mapped_status,
                    slippage_bps=slip_bps,
                    updated_at=_now(),
                )
            )
            conn.commit()
            ticker_filled += filled_qty

        # Update portfolio_positions with total filled shares
        if not dry_run and ticker_filled > 0:
            avg_price = (sum(fill_prices) / len(fill_prices)) if fill_prices else current_price
            new_shares = cur_shares.get(ticker, 0.0) + (
                ticker_filled if action in ("BUY", "COVER") else -ticker_filled
            )
            if new_shares <= 0.5:
                conn.execute(
                    portfolio_positions.delete().where(portfolio_positions.c.ticker == ticker)
                )
            else:
                conn.execute(
                    portfolio_positions.update()
                    .where(portfolio_positions.c.ticker == ticker)
                    .values(
                        shares=abs(new_shares),
                        current_price=avg_price,
                        updated_at=_now(),
                    )
                )
            conn.commit()

        results.append(
            {
                "ticker": ticker,
                "action": action,
                "ordered_shares": abs(delta_shares),
                "filled_shares": ticker_filled,
                "status": "DRY_RUN" if dry_run else ticker_status,
            }
        )

    return results
