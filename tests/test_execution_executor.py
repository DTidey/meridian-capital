"""Tests for execution/executor.py — limit pricing, chunking, dry-run, partial fills."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa

import analysis.db  # noqa: F401
import execution.db  # noqa: F401
import factors.db  # noqa: F401
import portfolio.db  # noqa: F401
import risk.db  # noqa: F401
from data.db import daily_prices, initialise_schema
from execution.db import execution_orders
from execution.executor import _chunk_orders, _limit_price, execute_approvals
from portfolio.db import portfolio_positions, position_approvals

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DATE = "2026-05-06"
_NAV = 10_000_000.0


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
    conn.execute(
        portfolio_positions.insert().values(
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
        )
    )
    conn.commit()


def _insert_approval(
    conn, ticker, action="BUY", target=200.0, current=100.0, status="APPROVED", close=100.0
):
    if close is not None:  # limits come from the stored close for the score date
        conn.execute(daily_prices.insert().values(ticker=ticker, date=_DATE, close=close))
    conn.execute(
        position_approvals.insert().values(
            rebalance_date=_DATE,
            ticker=ticker,
            action=action,
            target_shares=target,
            current_shares=current,
            delta_shares=target - current,
            status=status,
            created_at=f"{_DATE}T09:00:00",
        )
    )
    conn.commit()


def _base_config():
    return {
        "execution": {
            "limit_slippage_pct": 0.005,
            "max_adv_pct": 0.02,
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
    def test_filled_order_leaves_positions_to_book_sync(self, conn, tmp_path):
        """Packet 44: fills are recorded by the book sync, not by the executor."""
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
            results = execute_approvals(
                conn, client, _DATE, _base_config(), tmp_path, dry_run=False
            )

        assert len(results) == 1
        row = conn.execute(
            sa.select(portfolio_positions).where(portfolio_positions.c.ticker == "GOOG")
        ).fetchone()
        assert row is not None
        assert row.shares == pytest.approx(50.0)  # unchanged until the next book sync

    def test_not_shortable_skipped(self, conn, tmp_path):
        _insert_position(conn, "GME", shares=0.0, current_price=15.0)
        _insert_approval(conn, "GME", action="SHORT", target=100.0, current=0.0)

        client = MagicMock()
        with patch("execution.executor.is_shortable", return_value=False):
            results = execute_approvals(
                conn, client, _DATE, _base_config(), tmp_path, dry_run=False
            )

        assert len(results) == 1
        assert results[0]["status"] == "SKIPPED"
        client.submit_order.assert_not_called()


def _filled_client(symbol_positions=(), open_orders=()):
    client = MagicMock()
    client.get_all_positions.return_value = list(symbol_positions)
    client.get_orders.return_value = list(open_orders)
    client.submit_order.return_value = MagicMock(id="order-1", status="accepted", filled_qty="0")
    return client


def _broker_pos(symbol, qty):
    return MagicMock(symbol=symbol, qty=str(qty))


class TestBookOfRecord:
    def test_order_uses_alpaca_symbol(self, conn, tmp_path):
        _insert_approval(conn, "BRK-B", action="BUY", target=5.0, current=0.0)
        _insert_position(conn, "BRK-B", shares=0.0, current_price=400.0)
        client = _filled_client()
        execute_approvals(conn, client, _DATE, _base_config(), tmp_path, dry_run=False)
        req = client.submit_order.call_args.args[0]
        assert req.symbol == "BRK.B"

    def test_closing_guard_matches_alpaca_symbol(self, conn, tmp_path):
        # Adversarial: broker holds BRK.B; a SELL of BRK-B must not be skipped as stale.
        _insert_position(conn, "BRK-B", shares=5.0, current_price=400.0)
        _insert_approval(conn, "BRK-B", action="SELL", target=0.0, current=5.0)
        client = _filled_client([_broker_pos("BRK.B", 5)])
        results = execute_approvals(conn, client, _DATE, _base_config(), tmp_path, dry_run=False)
        assert results[0]["status"] != "SKIPPED"
        client.submit_order.assert_called_once()

    def test_stale_close_skipped_without_touching_positions(self, conn, tmp_path):
        _insert_position(conn, "GOOG", shares=50.0, current_price=100.0)
        _insert_approval(conn, "GOOG", action="SELL", target=0.0, current=50.0)
        client = _filled_client([])
        results = execute_approvals(conn, client, _DATE, _base_config(), tmp_path, dry_run=False)
        assert results[0]["status"] == "SKIPPED"
        row = conn.execute(
            sa.select(portfolio_positions).where(portfolio_positions.c.ticker == "GOOG")
        ).fetchone()
        assert row is not None and row.shares == pytest.approx(50.0)

    def test_filled_short_does_not_delete_position(self, conn, tmp_path):
        # Adversarial: the old post-fill update deleted every short (new_shares <= 0.5).
        _insert_position(conn, "TSLA", shares=-10.0, current_price=200.0, direction="SHORT")
        _insert_approval(conn, "TSLA", action="SHORT", target=-15.0, current=-10.0)
        client = _filled_client([_broker_pos("TSLA", -10)])
        with patch("execution.executor.is_shortable", return_value=True):
            execute_approvals(conn, client, _DATE, _base_config(), tmp_path, dry_run=False)
        row = conn.execute(
            sa.select(portfolio_positions).where(portfolio_positions.c.ticker == "TSLA")
        ).fetchone()
        assert row is not None and row.shares == pytest.approx(-10.0)

    def test_cover_guard_reads_side_when_qty_unsigned(self, conn, tmp_path):
        # Adversarial: Alpaca may report a short's qty unsigned; the side decides.
        _insert_position(conn, "TSLA", shares=-5.0, current_price=200.0, direction="SHORT")
        _insert_approval(conn, "TSLA", action="COVER", target=0.0, current=-5.0)
        short = MagicMock(symbol="TSLA", qty="5")
        short.side = "short"
        client = _filled_client([short])
        results = execute_approvals(conn, client, _DATE, _base_config(), tmp_path, dry_run=False)
        assert results[0]["status"] != "SKIPPED"
        client.submit_order.assert_called_once()


def _order_rows(conn, ticker):
    return conn.execute(
        sa.select(execution_orders).where(execution_orders.c.ticker == ticker)
    ).fetchall()


class TestSubmission:
    """Packet 49: queued DAY orders are recorded, not polled."""

    def test_records_alpaca_status_without_polling(self, conn, tmp_path):
        _insert_approval(conn, "AAPL", action="BUY", target=10.0, current=0.0)
        client = _filled_client()
        with patch("time.sleep") as sleep:
            results = execute_approvals(
                conn, client, _DATE, _base_config(), tmp_path, dry_run=False
            )
        sleep.assert_not_called()
        client.get_order_by_id.assert_not_called()
        assert results[0]["status"] == "SUBMITTED"
        [row] = _order_rows(conn, "AAPL")
        assert row.order_id == "order-1"
        assert row.status == "PENDING"

    def test_rejected_at_submission_is_failed(self, conn, tmp_path):
        _insert_approval(conn, "AAPL", action="BUY", target=10.0, current=0.0)
        client = _filled_client()
        client.submit_order.return_value = MagicMock(id="o-2", status="rejected", filled_qty="0")
        results = execute_approvals(conn, client, _DATE, _base_config(), tmp_path, dry_run=False)
        assert results[0]["status"] == "FAILED"
        assert _order_rows(conn, "AAPL")[0].status == "FAILED"

    def test_submit_error_is_failed_not_cancelled(self, conn, tmp_path):
        # Adversarial: nothing in the executor may write CANCELLED.
        _insert_approval(conn, "AAPL", action="BUY", target=10.0, current=0.0)
        client = _filled_client()
        client.submit_order.side_effect = RuntimeError("alpaca down")
        execute_approvals(conn, client, _DATE, _base_config(), tmp_path, dry_run=False)
        assert [r.status for r in _order_rows(conn, "AAPL")] == ["FAILED"]

    def test_limit_from_close_for_score_date(self, conn, tmp_path):
        # A position's stale current_price must not set the limit.
        _insert_position(conn, "AAPL", shares=5.0, current_price=150.0)
        _insert_approval(conn, "AAPL", action="BUY", target=10.0, current=5.0, close=200.0)
        conn.execute(daily_prices.insert().values(ticker="AAPL", date="2026-05-05", close=190.0))
        conn.commit()
        client = _filled_client()
        execute_approvals(conn, client, _DATE, _base_config(), tmp_path, dry_run=False)
        req = client.submit_order.call_args.args[0]
        assert req.limit_price == pytest.approx(201.0)  # 200 * 1.005

    def test_no_close_for_date_skipped(self, conn, tmp_path):
        _insert_approval(conn, "AAPL", action="BUY", target=10.0, current=0.0, close=None)
        conn.execute(daily_prices.insert().values(ticker="AAPL", date="2026-05-05", close=190.0))
        conn.commit()
        client = _filled_client()
        results = execute_approvals(conn, client, _DATE, _base_config(), tmp_path, dry_run=False)
        assert results[0] == {
            "ticker": "AAPL",
            "action": "BUY",
            "status": "SKIPPED",
            "reason": "no_close",
        }
        client.submit_order.assert_not_called()
        assert _order_rows(conn, "AAPL") == []

    def test_open_order_skipped(self, conn, tmp_path):
        _insert_approval(conn, "BRK-B", action="BUY", target=5.0, current=0.0)
        client = _filled_client(open_orders=[MagicMock(symbol="BRK.B")])
        results = execute_approvals(conn, client, _DATE, _base_config(), tmp_path, dry_run=False)
        assert results[0]["reason"] == "open_order"
        client.submit_order.assert_not_called()

    def test_open_orders_unavailable_raises(self, conn, tmp_path):
        _insert_approval(conn, "AAPL", action="BUY", target=10.0, current=0.0)
        client = _filled_client()
        client.get_orders.side_effect = RuntimeError("alpaca down")
        with pytest.raises(RuntimeError):
            execute_approvals(conn, client, _DATE, _base_config(), tmp_path, dry_run=False)
        client.submit_order.assert_not_called()
        assert _order_rows(conn, "AAPL") == []

    def test_dry_run_does_not_list_open_orders(self, conn, tmp_path):
        _insert_approval(conn, "AAPL", action="BUY", target=10.0, current=0.0)
        client = _filled_client()
        client.get_orders.side_effect = RuntimeError("must not be called")
        results = execute_approvals(conn, client, _DATE, _base_config(), tmp_path, dry_run=True)
        assert results[0]["status"] == "DRY_RUN"

    def test_open_orders_listed_with_max_limit(self, conn, tmp_path):
        # Alpaca returns 50 orders unless asked for more; the guard asks for its maximum.
        _insert_approval(conn, "AAPL", action="BUY", target=10.0, current=0.0)
        client = _filled_client()
        execute_approvals(conn, client, _DATE, _base_config(), tmp_path, dry_run=False)
        assert client.get_orders.call_args.args[0].limit == 500

    def test_truncated_open_orders_raises(self, conn, tmp_path):
        # Adversarial: a full page may hide more open orders, so nothing is submitted.
        _insert_approval(conn, "AAPL", action="BUY", target=10.0, current=0.0)
        client = _filled_client(open_orders=[MagicMock(symbol=f"T{i}") for i in range(500)])
        with pytest.raises(RuntimeError):
            execute_approvals(conn, client, _DATE, _base_config(), tmp_path, dry_run=False)
        client.submit_order.assert_not_called()


class TestTradeDirectionGuard:
    """Packet 56: no zero-share or wrong-side orders reach Alpaca."""

    def test_hold_skipped(self, conn, tmp_path):
        _insert_approval(conn, "ZTS", action="HOLD", target=-51.0, current=-51.0)
        client = _filled_client()
        results = execute_approvals(conn, client, _DATE, _base_config(), tmp_path, dry_run=False)
        assert (results[0]["status"], results[0]["reason"]) == ("SKIPPED", "no_change")
        client.submit_order.assert_not_called()
        assert _order_rows(conn, "ZTS") == []

    def test_zero_delta_skipped(self, conn, tmp_path):
        # 2026-10-29 TXN: stored delta 0.0 was read as missing and a qty 0 order was sent.
        _insert_approval(conn, "TXN", action="BUY", target=13.0, current=13.0)
        client = _filled_client()
        results = execute_approvals(conn, client, _DATE, _base_config(), tmp_path, dry_run=False)
        assert (results[0]["status"], results[0]["reason"]) == ("SKIPPED", "no_change")
        client.submit_order.assert_not_called()

    def test_action_mismatch_refused(self, conn, tmp_path, caplog):
        # 2026-10-29 BA: SHORT with delta +3 was sent as SHORT 3.
        _insert_approval(conn, "BA", action="SHORT", target=-11.0, current=-14.0)
        client = _filled_client()
        results = execute_approvals(conn, client, _DATE, _base_config(), tmp_path, dry_run=False)
        assert (results[0]["status"], results[0]["reason"]) == ("SKIPPED", "action_mismatch")
        client.submit_order.assert_not_called()
        assert any(r.levelname == "ERROR" and "BA" in r.getMessage() for r in caplog.records)

    def test_buy_with_negative_delta_refused(self, conn, tmp_path):
        # Adversarial: the long-side mirror (2026-10-28 and 10-29 AVY).
        _insert_approval(conn, "AVY", action="BUY", target=6.0, current=7.0)
        client = _filled_client()
        results = execute_approvals(conn, client, _DATE, _base_config(), tmp_path, dry_run=False)
        assert results[0]["reason"] == "action_mismatch"
        client.submit_order.assert_not_called()

    def test_sub_share_delta_skipped(self, conn, tmp_path):
        # Adversarial: 0.4 shares rounds to a qty 0 order at submission.
        _insert_approval(conn, "AKAM", action="COVER", target=-25.6, current=-26.0)
        client = _filled_client([_broker_pos("AKAM", -26)])
        results = execute_approvals(conn, client, _DATE, _base_config(), tmp_path, dry_run=False)
        assert results[0]["reason"] == "no_change"
        client.submit_order.assert_not_called()

    def test_consistent_cover_submitted(self, conn, tmp_path):
        _insert_approval(conn, "BA", action="COVER", target=-11.0, current=-14.0)
        client = _filled_client([_broker_pos("BA", -14)])
        results = execute_approvals(conn, client, _DATE, _base_config(), tmp_path, dry_run=False)
        assert results[0]["status"] == "SUBMITTED"
        assert client.submit_order.call_args.args[0].qty == 3
