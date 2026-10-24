"""Tests for execution/broker.py — broker positions and market clock."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa

import analysis.db  # noqa: F401
import execution.db  # noqa: F401
import factors.db  # noqa: F401
import portfolio.db  # noqa: F401
import risk.db  # noqa: F401
from data.db import initialise_schema
from execution.broker import get_broker_positions, market_is_open


@pytest.fixture
def mem_engine():
    engine = sa.create_engine("sqlite:///:memory:", future=True)
    initialise_schema(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def conn(mem_engine):
    c = mem_engine.connect()
    yield c
    c.close()


def _mock_position(symbol, qty, side="long"):
    p = MagicMock()
    p.symbol = symbol
    p.qty = str(abs(qty))
    p.side = side
    return p


class TestGetBrokerPositions:
    def test_long_position(self):
        client = MagicMock()
        client.get_all_positions.return_value = [_mock_position("AAPL", 100, "long")]
        result = get_broker_positions(client)
        assert result["AAPL"] == pytest.approx(100.0)

    def test_short_position_is_negative(self):
        client = MagicMock()
        client.get_all_positions.return_value = [_mock_position("GME", 50, "short")]
        result = get_broker_positions(client)
        assert result["GME"] == pytest.approx(-50.0)

    def test_alpaca_symbol_mapped_to_db_ticker(self):
        client = MagicMock()
        client.get_all_positions.return_value = [_mock_position("BRK.B", 5, "short")]
        assert get_broker_positions(client) == {"BRK-B": pytest.approx(-5.0)}

    def test_empty_positions(self):
        client = MagicMock()
        client.get_all_positions.return_value = []
        assert get_broker_positions(client) == {}


class TestMarketIsOpen:
    def test_open_returns_true(self):
        client = MagicMock()
        client.get_clock.return_value = MagicMock(is_open=True)
        assert market_is_open(client) is True

    def test_closed_returns_false_and_warns(self, caplog):
        import logging

        client = MagicMock()
        client.get_clock.return_value = MagicMock(is_open=False, next_open="2026-05-07T09:30:00")
        with caplog.at_level(logging.WARNING):
            result = market_is_open(client)
        assert result is False
        assert "CLOSED" in caplog.text
