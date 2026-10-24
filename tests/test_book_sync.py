"""Packet 44 (C10): portfolio_positions rebuilt from Alpaca, the book of record."""

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
import reporting.db  # noqa: F401
import risk.db  # noqa: F401
from analysis.db import combined_scores
from data.db import daily_prices, initialise_schema, sp500_universe
from execution.book_sync import sync_book
from portfolio.db import portfolio_history, portfolio_positions
from reporting.db import portfolio_nav

_DATE = "2026-10-26"


@pytest.fixture
def conn():
    engine = sa.create_engine("sqlite:///:memory:", future=True)
    initialise_schema(engine)
    c = engine.connect()
    yield c
    c.close()
    engine.dispose()


def _pos(symbol, qty, side="long", entry=100.0, price=110.0):
    # Alpaca may report short qty unsigned; the side decides the sign.
    return SimpleNamespace(
        symbol=symbol,
        qty=str(qty),
        side=SimpleNamespace(value=side),
        avg_entry_price=str(entry),
        current_price=None if price is None else str(price),
    )


def _client(positions, equity=100_000.0):
    client = MagicMock()
    client.get_all_positions.return_value = positions
    client.get_account.return_value = SimpleNamespace(equity=str(equity))
    return client


def _existing(conn, ticker, shares, direction, entry_date="2026-09-01"):
    conn.execute(
        portfolio_positions.insert().values(
            ticker=ticker, direction=direction, shares=shares, entry_date=entry_date
        )
    )
    conn.commit()


def _positions(conn):
    rows = conn.execute(sa.select(portfolio_positions)).fetchall()
    return {r.ticker: r for r in rows}


def _history(conn, date=_DATE):
    return conn.execute(
        sa.select(portfolio_history).where(portfolio_history.c.snapshot_date == date)
    ).fetchall()


class TestSyncBook:
    def test_positions_match_broker(self, conn):
        _existing(conn, "AAPL", 10.0, "LONG")
        summary = sync_book(conn, _client([_pos("AAPL", 12), _pos("MSFT", 5)]), _DATE)
        book = _positions(conn)
        assert set(book) == {"AAPL", "MSFT"}
        assert book["AAPL"].shares == pytest.approx(12.0)
        assert summary == {
            "positions": 2,
            "added": ["MSFT"],
            "removed": [],
            "changed": ["AAPL"],
            "equity": 100_000.0,
        }

    def test_short_is_signed(self, conn):
        sync_book(conn, _client([_pos("BRK.B", 5, "short", entry=400.0, price=380.0)]), _DATE)
        row = _positions(conn)["BRK-B"]
        assert row.direction == "SHORT"
        assert row.shares == pytest.approx(-5.0)
        assert row.market_value == pytest.approx(-1900.0)
        assert row.weight == pytest.approx(-0.019)
        # Short gains when the price falls: (380 - 400) * -5 = +100.
        assert row.unrealized_pnl == pytest.approx(100.0)

    def test_negative_qty_from_api_still_one_sign(self, conn):
        # Adversarial: a signed qty must not be negated twice.
        sync_book(conn, _client([_pos("TSLA", -7, "short")]), _DATE)
        assert _positions(conn)["TSLA"].shares == pytest.approx(-7.0)

    def test_stale_rows_removed(self, conn):
        _existing(conn, "OLD", 10.0, "LONG")
        summary = sync_book(conn, _client([]), _DATE)
        assert _positions(conn) == {}
        assert summary["removed"] == ["OLD"]
        assert _history(conn) == []

    def test_metadata_filled(self, conn):
        conn.execute(
            sp500_universe.insert().values(ticker="AAPL", company_name="Apple", gics_sector="IT")
        )
        for d, s in [("2026-10-22", 85.0), ("2026-10-23", 91.0), ("2026-10-27", 10.0)]:
            conn.execute(
                combined_scores.insert().values(
                    ticker="AAPL", score_date=d, combined_score=s, direction="LONG"
                )
            )
        conn.commit()
        sync_book(conn, _client([_pos("AAPL", 10, entry=100.0, price=120.0)]), _DATE)
        row = _positions(conn)["AAPL"]
        assert row.sector == "IT"
        assert row.combined_score == pytest.approx(91.0)  # latest on or before the date
        assert row.beta == pytest.approx(1.0)  # no price history
        assert row.entry_price == pytest.approx(100.0)
        assert row.current_price == pytest.approx(120.0)
        assert row.unrealized_pnl == pytest.approx(200.0)

    def test_missing_price_falls_back_to_close(self, conn):
        conn.execute(
            daily_prices.insert().values(
                ticker="XYZ", date="2026-10-23", close=42.0, adj_close=42.0
            )
        )
        conn.commit()
        sync_book(conn, _client([_pos("XYZ", 2, price=None)]), _DATE)
        assert _positions(conn)["XYZ"].current_price == pytest.approx(42.0)

    def test_history_is_whole_book(self, conn):
        _existing(conn, "AAPL", 12.0, "LONG")  # unchanged position still snapshotted
        sync_book(conn, _client([_pos("AAPL", 12), _pos("MSFT", 3, "short")]), _DATE)
        rows = {r.ticker: r for r in _history(conn)}
        assert set(rows) == {"AAPL", "MSFT"}
        assert rows["MSFT"].shares == pytest.approx(-3.0)
        assert rows["MSFT"].weight < 0

    def test_resync_same_date_replaces_history(self, conn):
        client = _client([_pos("AAPL", 12), _pos("MSFT", 3)])
        sync_book(conn, client, _DATE)
        sync_book(conn, _client([_pos("AAPL", 12)]), _DATE)
        assert [r.ticker for r in _history(conn)] == ["AAPL"]

    def test_other_dates_history_kept(self, conn):
        sync_book(conn, _client([_pos("AAPL", 12)]), "2026-10-23")
        sync_book(conn, _client([_pos("MSFT", 1)]), _DATE)
        assert [r.ticker for r in _history(conn, "2026-10-23")] == ["AAPL"]

    def test_entry_date_kept_for_held_position(self, conn):
        _existing(conn, "AAPL", 10.0, "LONG", entry_date="2026-09-01")
        sync_book(conn, _client([_pos("AAPL", 15)]), _DATE)
        assert _positions(conn)["AAPL"].entry_date == "2026-09-01"

    def test_entry_date_reset_on_flip(self, conn):
        _existing(conn, "AAPL", 10.0, "LONG", entry_date="2026-09-01")
        sync_book(conn, _client([_pos("AAPL", 4, "short")]), _DATE)
        row = _positions(conn)["AAPL"]
        assert row.entry_date == _DATE
        assert row.shares == pytest.approx(-4.0)

    def test_entry_date_kept_for_held_short(self, conn):
        _existing(conn, "TSLA", -4.0, "SHORT", entry_date="2026-09-15")
        sync_book(conn, _client([_pos("TSLA", 6, "short")]), _DATE)
        assert _positions(conn)["TSLA"].entry_date == "2026-09-15"

    def test_history_carries_price_and_pnl(self, conn):
        sync_book(conn, _client([_pos("AAPL", 10, entry=100.0, price=120.0)]), _DATE)
        row = _history(conn)[0]
        assert row.price == pytest.approx(120.0)
        assert row.unrealized_pnl == pytest.approx(200.0)
        assert row.market_value == pytest.approx(1200.0)

    def test_no_price_or_close_falls_back_to_entry(self, conn):
        sync_book(conn, _client([_pos("NEW", 3, entry=50.0, price=None)]), _DATE)
        row = _positions(conn)["NEW"]
        assert row.current_price == pytest.approx(50.0)
        assert row.unrealized_pnl == pytest.approx(0.0)

    def test_dry_run_writes_nothing(self, conn):
        _existing(conn, "OLD", 10.0, "LONG")
        summary = sync_book(conn, _client([_pos("AAPL", 1)]), _DATE, dry_run=True)
        assert set(_positions(conn)) == {"OLD"}
        assert _history(conn) == []
        assert summary["added"] == ["AAPL"] and summary["removed"] == ["OLD"]

    def test_zero_equity_leaves_weight_null(self, conn):
        sync_book(conn, _client([_pos("AAPL", 1)], equity=0.0), _DATE)
        assert _positions(conn)["AAPL"].weight is None

    def test_broker_error_keeps_previous_book(self, conn):
        # Adversarial: an Alpaca failure must not leave a half-written book.
        _existing(conn, "AAPL", 10.0, "LONG")
        client = _client([])
        client.get_all_positions.side_effect = RuntimeError("alpaca down")
        with pytest.raises(RuntimeError):
            sync_book(conn, client, _DATE)
        assert set(_positions(conn)) == {"AAPL"}

    def test_write_failure_rolls_back(self, conn, monkeypatch):
        # Adversarial: a failure mid-write leaves the previous book intact.
        _existing(conn, "AAPL", 10.0, "LONG")
        import execution.book_sync as bs

        def boom(row, date):
            raise RuntimeError("history write failed")

        monkeypatch.setattr(bs, "_history_row", boom)
        with pytest.raises(RuntimeError):
            sync_book(conn, _client([_pos("MSFT", 1)]), _DATE)
        assert set(_positions(conn)) == {"AAPL"}


def _nav_rows(conn):
    return {r.date: r.nav for r in conn.execute(sa.select(portfolio_nav)).fetchall()}


class TestSyncNav:
    """Packet 50: the book sync records Alpaca equity as the day's NAV."""

    def test_equity_recorded_as_nav(self, conn):
        sync_book(conn, _client([_pos("AAPL", 1)], equity=101_234.5), _DATE)
        assert _nav_rows(conn) == {_DATE: pytest.approx(101_234.5)}

    def test_flat_book_still_records_nav(self, conn):
        sync_book(conn, _client([], equity=100_000.0), _DATE)
        assert _nav_rows(conn) == {_DATE: pytest.approx(100_000.0)}

    def test_resync_replaces_nav(self, conn):
        sync_book(conn, _client([], equity=100_000.0), _DATE)
        sync_book(conn, _client([], equity=99_000.0), _DATE)
        assert _nav_rows(conn) == {_DATE: pytest.approx(99_000.0)}

    def test_dry_run_records_no_nav(self, conn):
        sync_book(conn, _client([], equity=100_000.0), _DATE, dry_run=True)
        assert _nav_rows(conn) == {}

    def test_failed_commit_records_no_nav(self, conn, monkeypatch):
        # Adversarial: NAV and positions are written together or not at all; fail after the
        # NAV insert, at commit.
        def boom():
            raise RuntimeError("commit failed")

        monkeypatch.setattr(conn, "commit", boom)
        with pytest.raises(RuntimeError):
            sync_book(conn, _client([_pos("MSFT", 1)]), _DATE)
        monkeypatch.undo()
        assert _nav_rows(conn) == {}
        assert _positions(conn) == {}

    def test_resync_keeps_reporting_columns(self, conn):
        # A manual re-sync after reporting must not blank that night's drawdown.
        sync_book(conn, _client([], equity=100_000.0), _DATE)
        conn.execute(
            portfolio_nav.update()
            .where(portfolio_nav.c.date == _DATE)
            .values(spy_close=480.0, drawdown_pct=0.02)
        )
        conn.commit()
        sync_book(conn, _client([], equity=99_000.0), _DATE)
        row = conn.execute(sa.select(portfolio_nav)).fetchone()
        assert row.nav == pytest.approx(99_000.0)
        assert row.spy_close == pytest.approx(480.0) and row.drawdown_pct == pytest.approx(0.02)
