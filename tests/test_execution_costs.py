"""Tests for execution/costs.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import portfolio.db   # noqa: F401
import factors.db     # noqa: F401
import risk.db        # noqa: F401
import analysis.db    # noqa: F401
import execution.db   # noqa: F401

from datetime import date, timedelta

import pytest
import sqlalchemy as sa

from data.db import initialise_schema
from execution.db import execution_orders
from execution.costs import compute_slippage, slippage_stats


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


class TestComputeSlippage:
    def test_buy_adverse(self):
        # Ordered at limit 100.50, filled at 100.60 — paid more → adverse
        bps = compute_slippage(100.50, 100.60, "buy")
        assert bps == pytest.approx((0.10 / 100.50) * 10_000, abs=0.1)

    def test_buy_favorable(self):
        # Filled below limit — negative slippage (favorable)
        bps = compute_slippage(100.50, 100.40, "buy")
        assert bps < 0.0

    def test_sell_adverse(self):
        # Ordered at limit 99.50, filled at 99.40 — received less → adverse
        bps = compute_slippage(99.50, 99.40, "sell")
        assert bps > 0.0

    def test_zero_price_returns_zero(self):
        assert compute_slippage(0.0, 100.0, "buy") == pytest.approx(0.0)

    def test_cover_treated_as_buy(self):
        bps_cover = compute_slippage(50.0, 50.5, "cover")
        bps_buy   = compute_slippage(50.0, 50.5, "buy")
        assert bps_cover == pytest.approx(bps_buy)


class TestSlippageStats:
    def _insert_order(self, conn, ticker, slippage_bps, status="FILLED", days_ago=0):
        d = (date.today() - timedelta(days=days_ago)).isoformat()
        conn.execute(execution_orders.insert().values(
            rebalance_date=d,
            ticker=ticker,
            action="BUY",
            ordered_shares=100.0,
            filled_shares=100.0,
            avg_fill_price=150.0,
            order_id="abc",
            status=status,
            slippage_bps=slippage_bps,
            created_at=f"{d}T10:00:00",
            updated_at=f"{d}T10:05:00",
        ))
        conn.commit()

    def test_empty_returns_zeros(self, conn):
        stats = slippage_stats(conn)
        assert stats["count"] == 0
        assert stats["mean_bps"] == pytest.approx(0.0)

    def test_mean_and_worst(self, conn):
        self._insert_order(conn, "AAPL", 2.0)
        self._insert_order(conn, "MSFT", 4.0)
        self._insert_order(conn, "TSLA", 10.0)
        stats = slippage_stats(conn)
        assert stats["count"] == 3
        assert stats["mean_bps"] == pytest.approx((2 + 4 + 10) / 3, abs=0.01)
        assert stats["worst_ticker"] == "TSLA"

    def test_old_orders_excluded(self, conn):
        self._insert_order(conn, "OLD", 50.0, days_ago=35)
        self._insert_order(conn, "NEW", 5.0, days_ago=1)
        stats = slippage_stats(conn, days=30)
        assert stats["count"] == 1
        assert stats["worst_ticker"] == "NEW"

    def test_non_filled_excluded(self, conn):
        self._insert_order(conn, "CANC", 99.0, status="CANCELLED")
        stats = slippage_stats(conn)
        assert stats["count"] == 0
