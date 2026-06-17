"""Order state machine and SIGINT handler for graceful shutdown."""

from __future__ import annotations

import logging
import signal
import sys

log = logging.getLogger(__name__)


def cancel_open_orders(client) -> int:
    """Cancel all open Alpaca orders. Returns the count cancelled."""
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus

    try:
        cancel_statuses = client.cancel_orders()
        count = len(cancel_statuses) if cancel_statuses else 0
        log.info("Cancelled %d open orders.", count)
        return count
    except Exception as exc:
        log.error("Failed to cancel open orders: %s", exc)
        return 0


class OrderManager:
    """
    Context manager that installs a SIGINT handler.
    On Ctrl-C: cancels all open orders and exits gracefully.
    """

    def __init__(self, client):
        self._client = client
        self._original_handler = None

    def __enter__(self):
        self._original_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, self._handle_sigint)
        return self

    def __exit__(self, *_):
        signal.signal(signal.SIGINT, self._original_handler)

    def _handle_sigint(self, signum, frame):
        log.warning("Execution interrupted — cancelling open orders…")
        cancel_open_orders(self._client)
        log.warning("Open orders cancelled. Exiting.")
        sys.exit(1)
