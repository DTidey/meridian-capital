"""Tests for execution/order_manager.py — cancel_open_orders and OrderManager."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import signal
from unittest.mock import MagicMock

import pytest

from execution.order_manager import OrderManager, cancel_open_orders


class TestCancelOpenOrders:
    def test_returns_count(self):
        client = MagicMock()
        client.cancel_orders.return_value = [MagicMock(), MagicMock(), MagicMock()]
        assert cancel_open_orders(client) == 3

    def test_empty_returns_zero(self):
        client = MagicMock()
        client.cancel_orders.return_value = []
        assert cancel_open_orders(client) == 0

    def test_api_error_returns_zero(self):
        client = MagicMock()
        client.cancel_orders.side_effect = Exception("API error")
        assert cancel_open_orders(client) == 0

    def test_none_response_returns_zero(self):
        client = MagicMock()
        client.cancel_orders.return_value = None
        assert cancel_open_orders(client) == 0


class TestOrderManager:
    def test_context_manager_restores_handler(self):
        client = MagicMock()
        original = signal.getsignal(signal.SIGINT)
        with OrderManager(client):
            inside_handler = signal.getsignal(signal.SIGINT)
            assert inside_handler != original
        restored_handler = signal.getsignal(signal.SIGINT)
        assert restored_handler == original

    def test_sigint_calls_cancel(self):
        client = MagicMock()
        client.cancel_orders.return_value = []
        manager = OrderManager(client)
        manager.__enter__()
        with pytest.raises(SystemExit):
            manager._handle_sigint(signal.SIGINT, None)
        client.cancel_orders.assert_called_once()
        signal.signal(signal.SIGINT, signal.default_int_handler)
