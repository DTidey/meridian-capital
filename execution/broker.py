"""Alpaca broker client, broker positions, and market clock."""

from __future__ import annotations

import logging
import os

from execution.symbols import from_alpaca

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------


def get_client():
    """Return an Alpaca TradingClient using environment credentials."""
    from alpaca.trading.client import TradingClient

    api_key = os.environ.get("ALPACA_API_KEY", "")
    secret = os.environ.get("ALPACA_SECRET_KEY", "")
    paper = os.environ.get("ALPACA_PAPER", "true").lower() != "false"

    if not api_key or not secret:
        raise OSError("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in environment.")
    return TradingClient(api_key, secret, paper=paper)


def get_account(client) -> dict:
    """Return basic account info dict."""
    acct = client.get_account()
    return {
        "equity": float(acct.equity),
        "buying_power": float(acct.buying_power),
        "status": str(acct.status),
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
    """Return {db ticker: signed_qty} from Alpaca (positive=long, negative=short)."""
    positions = client.get_all_positions()
    result: dict[str, float] = {}
    for p in positions:
        qty = float(p.qty)
        if p.side.lower() == "short":
            qty = -abs(qty)
        result[from_alpaca(p.symbol)] = qty
    return result
