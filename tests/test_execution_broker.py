"""Tests for execution/broker.py — position reconciliation and market clock."""

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
from execution.broker import (
    get_broker_positions,
    market_is_open,
    reconcile_positions,
)
from portfolio.db import portfolio_positions


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


def _insert_position(conn, ticker, shares, direction="LONG"):
    conn.execute(
        portfolio_positions.insert().values(
            ticker=ticker,
            direction=direction,
            shares=shares,
            entry_price=100.0,
            entry_date="2026-01-01",
            current_price=100.0,
            market_value=shares * 100.0,
            weight=0.05,
            unrealized_pnl=0.0,
            sector="Technology",
            combined_score=60.0,
            beta=1.0,
            updated_at="2026-05-06T10:00:00",
        )
    )
    conn.commit()


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


class TestReconcilePositions:
    def test_no_discrepancy(self, conn):
        _insert_position(conn, "AAPL", 100.0, "LONG")
        client = MagicMock()
        client.get_all_positions.return_value = [_mock_position("AAPL", 100, "long")]
        corrections = reconcile_positions(conn, client)
        assert corrections == []

    def test_broker_has_more_shares_auto_corrects(self, conn, tmp_path):
        _insert_position(conn, "MSFT", 50.0, "LONG")
        client = MagicMock()
        client.get_all_positions.return_value = [_mock_position("MSFT", 100, "long")]
        corrections = reconcile_positions(conn, client)
        assert len(corrections) == 1
        assert corrections[0]["ticker"] == "MSFT"
        assert corrections[0]["action"] == "corrected"
        # DB should now reflect broker qty
        row = conn.execute(
            sa.select(portfolio_positions).where(portfolio_positions.c.ticker == "MSFT")
        ).fetchone()
        assert row.shares == pytest.approx(100.0)

    def test_broker_closed_position_removes_from_db(self, conn):
        _insert_position(conn, "TSLA", 75.0, "LONG")
        client = MagicMock()
        # Broker has zero (position was closed)
        client.get_all_positions.return_value = []
        corrections = reconcile_positions(conn, client)
        assert len(corrections) == 1
        row = conn.execute(
            sa.select(portfolio_positions).where(portfolio_positions.c.ticker == "TSLA")
        ).fetchone()
        assert row is None

    def test_small_discrepancy_ignored(self, conn):
        _insert_position(conn, "GOOG", 100.0, "LONG")
        client = MagicMock()
        # 0.3 share difference — under 0.5 threshold
        p = _mock_position("GOOG", 100, "long")
        p.qty = "100.3"
        client.get_all_positions.return_value = [p]
        corrections = reconcile_positions(conn, client)
        assert corrections == []

    def test_new_broker_position_inserted(self, conn):
        client = MagicMock()
        client.get_all_positions.return_value = [_mock_position("NVDA", 200, "long")]
        corrections = reconcile_positions(conn, client)
        assert len(corrections) == 1
        row = conn.execute(
            sa.select(portfolio_positions).where(portfolio_positions.c.ticker == "NVDA")
        ).fetchone()
        assert row is not None
        assert row.shares == pytest.approx(200.0)
