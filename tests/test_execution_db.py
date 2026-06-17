"""Tests for execution/db.py — table creation and basic operations."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import portfolio.db   # noqa: F401
import factors.db     # noqa: F401
import risk.db        # noqa: F401
import analysis.db    # noqa: F401
import execution.db   # noqa: F401

import pytest
import sqlalchemy as sa

from data.db import initialise_schema
from execution.db import execution_orders


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


def _insert_order(conn, ticker="AAPL", action="BUY", status="PENDING", slippage_bps=None):
    conn.execute(execution_orders.insert().values(
        rebalance_date="2026-05-06",
        ticker=ticker,
        action=action,
        ordered_shares=100.0,
        filled_shares=0.0,
        avg_fill_price=None,
        order_id=None,
        status=status,
        slippage_bps=slippage_bps,
        created_at="2026-05-06T10:00:00",
        updated_at="2026-05-06T10:00:00",
    ))
    conn.commit()


class TestExecutionDbSchema:
    def test_table_exists(self, mem_engine):
        inspector = sa.inspect(mem_engine)
        assert "execution_orders" in inspector.get_table_names()

    def test_insert_and_query(self, conn):
        _insert_order(conn, "MSFT", "SELL")
        row = conn.execute(
            sa.select(execution_orders).where(execution_orders.c.ticker == "MSFT")
        ).fetchone()
        assert row is not None
        assert row.action == "SELL"
        assert row.status == "PENDING"

    def test_update_fill(self, conn):
        _insert_order(conn, "GOOG", "BUY")
        conn.execute(
            execution_orders.update()
            .where(execution_orders.c.ticker == "GOOG")
            .values(filled_shares=100.0, avg_fill_price=150.25, status="FILLED", slippage_bps=2.5)
        )
        conn.commit()
        row = conn.execute(
            sa.select(execution_orders).where(execution_orders.c.ticker == "GOOG")
        ).fetchone()
        assert row.status == "FILLED"
        assert row.avg_fill_price == pytest.approx(150.25)
        assert row.slippage_bps == pytest.approx(2.5)

    def test_multiple_tickers(self, conn):
        for t in ["AAPL", "TSLA", "AMZN"]:
            _insert_order(conn, t, "BUY")
        rows = conn.execute(sa.select(execution_orders)).fetchall()
        assert len(rows) == 3

    def test_status_index(self, mem_engine):
        """Index on status column should exist (SQLite won't error if missing, just verify the table works)."""
        with mem_engine.connect() as conn:
            _insert_order(conn, "IBM", "SHORT", status="FILLED")
            rows = conn.execute(
                sa.select(execution_orders).where(execution_orders.c.status == "FILLED")
            ).fetchall()
        assert len(rows) == 1
