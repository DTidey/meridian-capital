"""Tests for execution/executor.py — limit pricing, chunking, dry-run, partial fills."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import portfolio.db   # noqa: F401
import factors.db     # noqa: F401
import risk.db        # noqa: F401
import analysis.db    # noqa: F401
import execution.db   # noqa: F401

from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa

from data.db import initialise_schema
from portfolio.db import portfolio_positions, position_approvals
from execution.db import execution_orders
from execution.executor import _limit_price, _chunk_orders, execute_approvals


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DATE = "2026-05-06"
_NAV  = 10_000_000.0


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


def _insert_position(conn, ticker, shares=100.0, current_price=150.0, direction="LONG"):
    conn.execute(portfolio_positions.insert().values(
        ticker=ticker,
        direction=direction,
        shares=shares,
        entry_price=current_price,
        entry_date="2026-01-01",
        current_price=current_price,
        market_value=shares * current_price,
        weight=0.05,
        unrealized_pnl=0.0,
        sector="Technology",
        combined_score=60.0,
        beta=1.0,
        updated_at=f"{_DATE}T10:00:00",
    ))
    conn.commit()


def _insert_approval(conn, ticker, action="BUY", target=200.0, current=100.0, status="APPROVED"):
    conn.execute(position_approvals.insert().values(
        rebalance_date=_DATE,
        ticker=ticker,
        action=action,
        target_shares=target,
        current_shares=current,
        delta_shares=target - current,
        status=status,
        created_at=f"{_DATE}T09:00:00",
    ))
    conn.commit()


def _base_config():
    return {
        "execution": {
            "limit_slippage_pct":   0.005,
            "max_adv_pct":          0.02,
            "poll_timeout_s":       5,
            "poll_interval_s":      1,
            "shortable_cache_days": 7,
        }
    }


class TestLimitPrice:
    def test_buy_adds_slippage(self):
        p = _limit_price("BUY", 100.0, 0.005)
        assert p == pytest.approx(100.50, abs=0.01)

    def test_sell_subtracts_slippage(self):
        p = _limit_price("SELL", 100.0, 0.005)
        assert p == pytest.approx(99.50, abs=0.01)

    def test_cover_adds_slippage(self):
        p = _limit_price("COVER", 50.0, 0.005)
        assert p == pytest.approx(50.25, abs=0.01)

    def test_short_subtracts_slippage(self):
        p = _limit_price("SHORT", 50.0, 0.005)
        assert p == pytest.approx(49.75, abs=0.01)


class TestChunkOrders:
    def test_no_chunk_needed(self):
        chunks = _chunk_orders(100.0, adv=10_000.0, max_adv_pct=0.02)
        assert len(chunks) == 1
        assert chunks[0] == pytest.approx(100.0)

    def test_large_order_chunked(self):
        # 500 shares, ADV=10000, max 2% = 200 shares/chunk → 3 chunks (200, 200, 100)
        chunks = _chunk_orders(500.0, adv=10_000.0, max_adv_pct=0.02)
        assert len(chunks) == 3
        assert sum(chunks) == pytest.approx(500.0)
        assert max(chunks) == pytest.approx(200.0)

    def test_no_adv_single_chunk(self):
        chunks = _chunk_orders(1000.0, adv=None)
        assert len(chunks) == 1

    def test_negative_shares_handled(self):
        chunks = _chunk_orders(-300.0, adv=10_000.0, max_adv_pct=0.02)
        assert all(c > 0 for c in chunks)
        assert sum(chunks) == pytest.approx(300.0)


class TestExecuteApprovalsDryRun:
    def test_dry_run_no_alpaca_calls(self, conn, tmp_path):
        """Dry-run mode: orders logged but no Alpaca submit calls."""
        _insert_position(conn, "AAPL", shares=100.0, current_price=150.0)
        _insert_approval(conn, "AAPL", action="BUY", target=200.0, current=100.0)

        client = MagicMock()
        with patch("execution.executor.is_shortable", return_value=True):
            results = execute_approvals(conn, client, _DATE, _base_config(), tmp_path, dry_run=True)

        client.submit_order.assert_not_called()
        assert len(results) == 1
        assert results[0]["ticker"] == "AAPL"

    def test_no_approvals_returns_empty(self, conn, tmp_path):
        client = MagicMock()
        results = execute_approvals(conn, client, _DATE, _base_config(), tmp_path, dry_run=True)
        assert results == []

    def test_non_approved_skipped(self, conn, tmp_path):
        _insert_position(conn, "MSFT", shares=50.0, current_price=300.0)
        _insert_approval(conn, "MSFT", action="BUY", target=100.0, current=50.0, status="REJECTED")
        client = MagicMock()
        results = execute_approvals(conn, client, _DATE, _base_config(), tmp_path, dry_run=True)
        assert results == []


class TestExecuteApprovalsFilled:
    def test_filled_order_updates_portfolio(self, conn, tmp_path):
        """A fully filled order should update portfolio_positions shares."""
        _insert_position(conn, "GOOG", shares=50.0, current_price=100.0)
        _insert_approval(conn, "GOOG", action="BUY", target=100.0, current=50.0)

        client = MagicMock()
        mock_order = MagicMock()
        mock_order.id = "order-uuid-123"
        client.submit_order.return_value = mock_order

        filled_order = MagicMock()
        filled_order.status = "filled"
        filled_order.filled_qty = "50"
        filled_order.filled_avg_price = "100.25"
        client.get_order_by_id.return_value = filled_order

        with patch("execution.executor.is_shortable", return_value=True):
            results = execute_approvals(conn, client, _DATE, _base_config(), tmp_path, dry_run=False)

        assert len(results) == 1
        row = conn.execute(
            sa.select(portfolio_positions).where(portfolio_positions.c.ticker == "GOOG")
        ).fetchone()
        assert row is not None
        assert row.shares == pytest.approx(100.0)  # 50 + 50 filled

    def test_zero_fill_order_cancelled(self, conn, tmp_path):
        """Zero fill should result in CANCELLED execution_order row."""
        _insert_position(conn, "XYZ", shares=10.0, current_price=50.0)
        _insert_approval(conn, "XYZ", action="BUY", target=20.0, current=10.0)

        client = MagicMock()
        mock_order = MagicMock()
        mock_order.id = "order-uuid-zero"
        client.submit_order.return_value = mock_order

        zero_fill = MagicMock()
        zero_fill.status = "filled"
        zero_fill.filled_qty = "0"
        zero_fill.filled_avg_price = None
        client.get_order_by_id.return_value = zero_fill

        with patch("execution.executor.is_shortable", return_value=True):
            execute_approvals(conn, client, _DATE, _base_config(), tmp_path, dry_run=False)

        rows = conn.execute(
            sa.select(execution_orders.c.status).where(execution_orders.c.ticker == "XYZ")
        ).fetchall()
        assert any(r[0] == "CANCELLED" for r in rows)

    def test_not_shortable_skipped(self, conn, tmp_path):
        _insert_position(conn, "GME", shares=0.0, current_price=15.0)
        _insert_approval(conn, "GME", action="SHORT", target=100.0, current=0.0)

        client = MagicMock()
        with patch("execution.executor.is_shortable", return_value=False):
            results = execute_approvals(conn, client, _DATE, _base_config(), tmp_path, dry_run=False)

        assert len(results) == 1
        assert results[0]["status"] == "SKIPPED"
        client.submit_order.assert_not_called()
