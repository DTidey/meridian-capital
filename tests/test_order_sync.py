"""Packet 49 (C12): execution_orders follow Alpaca's view of each order."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import sqlalchemy as sa

import analysis.db  # noqa: F401
import execution.db  # noqa: F401
import factors.db  # noqa: F401
import portfolio.db  # noqa: F401
import risk.db  # noqa: F401
from data.db import initialise_schema
from execution.db import execution_orders
from execution.order_sync import db_status, sync_orders


@pytest.fixture
def conn():
    engine = sa.create_engine("sqlite:///:memory:", future=True)
    initialise_schema(engine)
    c = engine.connect()
    yield c
    c.close()
    engine.dispose()


def _row(conn, order_id, status="PENDING", action="BUY", ticker="AAPL", filled=0.0):
    conn.execute(
        execution_orders.insert().values(
            rebalance_date="2026-10-26",
            ticker=ticker,
            action=action,
            ordered_shares=10.0,
            filled_shares=filled,
            order_id=order_id,
            status=status,
            created_at="2026-10-26T22:30:00",
            updated_at="2026-10-26T22:30:00",
        )
    )
    conn.commit()


def _order(status, filled="0", avg=None, limit="100.50"):
    return SimpleNamespace(
        status=SimpleNamespace(value=status),
        filled_qty=filled,
        filled_avg_price=avg,
        limit_price=limit,
    )


def _client(orders: dict):
    client = MagicMock()
    client.get_order_by_id.side_effect = lambda oid: orders[oid]
    return client


def _get(conn, order_id):
    return conn.execute(
        sa.select(execution_orders).where(execution_orders.c.order_id == order_id)
    ).fetchone()


class TestDbStatus:
    @pytest.mark.parametrize(
        ("alpaca", "filled", "expected"),
        [
            ("filled", 10, "FILLED"),
            ("partially_filled", 4, "PARTIAL"),
            ("new", 0, "PENDING"),
            ("accepted", 0, "PENDING"),
            ("done_for_day", 0, "PENDING"),
            ("done_for_day", 3, "PARTIAL"),
            ("canceled", 0, "CANCELLED"),
            ("expired", 3, "CANCELLED"),
            ("rejected", 0, "FAILED"),
            ("replaced", 0, "CANCELLED"),
            # Adversarial: statuses that sound final but are not
            ("suspended", 0, "PENDING"),
            ("pending_cancel", 2, "PARTIAL"),
            ("stopped", 0, "PENDING"),
            ("calculated", 0, "PENDING"),
            ("held", 0, "PENDING"),
            ("pending_new", 0, "PENDING"),
        ],
    )
    def test_mapping(self, alpaca, filled, expected):
        assert db_status(alpaca, filled) == expected

    def test_enum_value_accepted(self):
        assert db_status(SimpleNamespace(value="filled"), 1) == "FILLED"


class TestSyncOrders:
    def test_filled_order_recorded_with_slippage(self, conn):
        _row(conn, "o1")
        summary = sync_orders(conn, _client({"o1": _order("filled", "10", "100.60")}))
        row = _get(conn, "o1")
        assert row.status == "FILLED"
        assert row.filled_shares == pytest.approx(10.0)
        assert row.avg_fill_price == pytest.approx(100.60)
        assert row.slippage_bps == pytest.approx((100.60 - 100.50) / 100.50 * 10_000)
        assert summary == {"checked": 1, "updated": 1, "final": 1}

    def test_partial_then_expired_keeps_fill(self, conn):
        _row(conn, "o1")
        sync_orders(conn, _client({"o1": _order("partially_filled", "4", "100.40")}))
        assert _get(conn, "o1").status == "PARTIAL"
        sync_orders(conn, _client({"o1": _order("expired", "4", "100.40")}))
        row = _get(conn, "o1")
        assert row.status == "CANCELLED"
        assert row.filled_shares == pytest.approx(4.0)

    def test_still_open_unchanged(self, conn):
        _row(conn, "o1")
        summary = sync_orders(conn, _client({"o1": _order("accepted")}))
        assert _get(conn, "o1").status == "PENDING"
        assert summary["updated"] == 0 and summary["final"] == 0

    def test_rejected_is_failed(self, conn):
        _row(conn, "o1")
        sync_orders(conn, _client({"o1": _order("rejected")}))
        assert _get(conn, "o1").status == "FAILED"

    def test_final_rows_not_fetched(self, conn):
        for oid, status in [("f", "FILLED"), ("c", "CANCELLED"), ("x", "FAILED"), ("d", "DRY_RUN")]:
            _row(conn, oid, status=status)
        client = _client({})
        assert sync_orders(conn, client)["checked"] == 0
        client.get_order_by_id.assert_not_called()

    def test_rows_without_order_id_skipped(self, conn):
        # Adversarial: pre-packet rows and failed submissions have no Alpaca id.
        _row(conn, None)
        client = _client({})
        assert sync_orders(conn, client)["checked"] == 0

    def test_fetch_error_leaves_row(self, conn):
        _row(conn, "bad", ticker="AAPL")
        _row(conn, "good", ticker="MSFT")

        def get(oid):
            if oid == "bad":
                raise RuntimeError("404")
            return _order("filled", "10", "100.50")

        client = MagicMock()
        client.get_order_by_id.side_effect = get
        sync_orders(conn, client)
        assert _get(conn, "bad").status == "PENDING"
        assert _get(conn, "good").status == "FILLED"

    def test_short_slippage_sign(self, conn):
        # Adversarial: a short filled below its limit received less, which is adverse (+).
        _row(conn, "o1", action="SHORT")
        sync_orders(conn, _client({"o1": _order("filled", "10", "100.00", limit="100.50")}))
        assert _get(conn, "o1").slippage_bps > 0

    def test_malformed_order_leaves_row(self, conn):
        # Adversarial: a value that cannot be parsed is isolated like a fetch error.
        _row(conn, "bad", ticker="AAPL")
        _row(conn, "good", ticker="MSFT")
        orders = {"bad": _order("filled", "ten"), "good": _order("filled", "10", "100.50")}
        sync_orders(conn, _client(orders))
        assert _get(conn, "bad").status == "PENDING"
        assert _get(conn, "good").status == "FILLED"

    def test_unchanged_partial_not_rewritten(self, conn):
        _row(conn, "o1", status="PARTIAL", filled=4.0)
        summary = sync_orders(conn, _client({"o1": _order("partially_filled", "4", "100.40")}))
        assert summary["updated"] == 0
        assert _get(conn, "o1").updated_at == "2026-10-26T22:30:00"

    def test_fill_without_limit_has_no_slippage(self, conn):
        _row(conn, "o1")
        sync_orders(conn, _client({"o1": _order("filled", "10", "100.60", limit=None)}))
        row = _get(conn, "o1")
        assert row.status == "FILLED" and row.slippage_bps is None
