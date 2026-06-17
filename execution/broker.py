"""Alpaca broker client, position reconciliation, and market clock."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

import sqlalchemy as sa

from portfolio.db import portfolio_positions

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------

def get_client():
    """Return an Alpaca TradingClient using environment credentials."""
    from alpaca.trading.client import TradingClient

    api_key = os.environ.get("ALPACA_API_KEY", "")
    secret  = os.environ.get("ALPACA_SECRET_KEY", "")
    paper   = os.environ.get("ALPACA_PAPER", "true").lower() != "false"

    if not api_key or not secret:
        raise EnvironmentError(
            "ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in environment."
        )
    return TradingClient(api_key, secret, paper=paper)


def get_account(client) -> dict:
    """Return basic account info dict."""
    acct = client.get_account()
    return {
        "equity":          float(acct.equity),
        "buying_power":    float(acct.buying_power),
        "day_trade_count": int(acct.daytrade_count),
        "status":          str(acct.status),
    }


# ---------------------------------------------------------------------------
# Market clock
# ---------------------------------------------------------------------------

def market_is_open(client) -> bool:
    """Return True if market is currently open. Logs WARNING when closed."""
    clock = client.get_clock()
    if not clock.is_open:
        log.warning(
            "Market is CLOSED (next open: %s). Orders will be submitted but "
            "may not fill until next session.",
            clock.next_open,
        )
    return clock.is_open


# ---------------------------------------------------------------------------
# Broker positions
# ---------------------------------------------------------------------------

def get_broker_positions(client) -> dict[str, float]:
    """Return {ticker: signed_qty} from Alpaca (positive=long, negative=short)."""
    positions = client.get_all_positions()
    result: dict[str, float] = {}
    for p in positions:
        qty = float(p.qty)
        if p.side.lower() == "short":
            qty = -abs(qty)
        result[p.symbol] = qty
    return result


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

def reconcile_positions(conn, client, cache_dir=None, dry_run: bool = False) -> list[dict]:
    """
    Compare Alpaca broker positions against portfolio_positions.
    Auto-correct any discrepancy > 0.5 shares and return list of corrections.
    When dry_run=True, logs discrepancies but does not write to the DB.
    """
    broker_qtys = get_broker_positions(client)

    rows = conn.execute(
        sa.select(
            portfolio_positions.c.ticker,
            portfolio_positions.c.shares,
            portfolio_positions.c.direction,
        )
    ).fetchall()

    db_by_ticker: dict[str, float] = {}
    for row in rows:
        ticker, shares, direction = row
        signed = -abs(float(shares or 0.0)) if direction == "SHORT" else float(shares or 0.0)
        db_by_ticker[ticker] = signed

    all_tickers = set(broker_qtys) | set(db_by_ticker)
    corrections: list[dict] = []
    now_str = datetime.now(timezone.utc).isoformat()

    for ticker in all_tickers:
        bq = broker_qtys.get(ticker, 0.0)
        dq = db_by_ticker.get(ticker, 0.0)

        if abs(bq - dq) <= 0.5:
            continue

        action = "would_correct" if dry_run else "corrected"
        log.warning(
            "Position discrepancy for %s: broker=%.2f db=%.2f — %s.",
            ticker, bq, dq, "DRY-RUN (no DB change)" if dry_run else "auto-correcting DB",
        )

        if not dry_run:
            if bq == 0.0:
                # Position closed at broker — remove from DB
                conn.execute(
                    portfolio_positions.delete().where(portfolio_positions.c.ticker == ticker)
                )
            elif ticker in db_by_ticker:
                # Update existing row
                direction = "SHORT" if bq < 0 else "LONG"
                conn.execute(
                    portfolio_positions.update()
                    .where(portfolio_positions.c.ticker == ticker)
                    .values(shares=abs(bq), direction=direction, updated_at=now_str)
                )
            else:
                # New position at broker not in DB — insert skeleton row
                direction = "SHORT" if bq < 0 else "LONG"
                conn.execute(
                    portfolio_positions.insert().values(
                        ticker=ticker,
                        direction=direction,
                        shares=abs(bq),
                        entry_price=None,
                        entry_date=now_str[:10],
                        current_price=None,
                        market_value=None,
                        weight=None,
                        unrealized_pnl=None,
                        sector=None,
                        combined_score=None,
                        beta=None,
                        updated_at=now_str,
                    )
                )
            conn.commit()

        corrections.append({
            "ticker":      ticker,
            "broker_qty":  bq,
            "db_qty":      dq,
            "action":      action,
            "corrected_at": now_str,
        })

    return corrections
