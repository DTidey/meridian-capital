"""Earnings calendar — today-skip logic and DB round-trips."""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
import sqlalchemy as sa

from data.db import earnings_calendar as ec_table
from data.earnings_calendar import update_earnings_calendar

_CONFIG = {"earnings_calendar": {"lookahead_days": 30}}


def _today() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


def _future(days: int) -> str:
    return (datetime.utcnow() + timedelta(days=days)).strftime("%Y-%m-%d")


def _insert_fetched(conn, ticker, earnings_date, fetched_at):
    conn.execute(
        ec_table.insert().values(
            ticker=ticker,
            earnings_date=earnings_date,
            eps_estimate=1.23,
            fetched_at=fetched_at,
        )
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Skip tickers already fetched today
# ---------------------------------------------------------------------------

class TestTodaySkip:
    def test_ticker_fetched_today_is_skipped(self, tmp_db):
        _insert_fetched(tmp_db, "AAPL", _future(10), _today() + "T09:00:00")

        with patch("data.earnings_calendar._fetch_earnings_date") as mock_fetch:
            mock_fetch.return_value = [{"earnings_date": _future(10), "eps_estimate": 1.5}]
            result = update_earnings_calendar(tmp_db, ["AAPL"], _CONFIG)

        mock_fetch.assert_not_called()
        assert result["AAPL"] == 0

    def test_ticker_not_fetched_today_is_fetched(self, tmp_db):
        with patch("data.earnings_calendar._fetch_earnings_date") as mock_fetch:
            mock_fetch.return_value = [{"earnings_date": _future(10), "eps_estimate": 1.5}]
            result = update_earnings_calendar(tmp_db, ["MSFT"], _CONFIG)

        mock_fetch.assert_called_once_with("MSFT")
        assert result["MSFT"] == 1

    def test_ticker_fetched_yesterday_is_refetched(self, tmp_db):
        yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d") + "T12:00:00"
        _insert_fetched(tmp_db, "GOOG", _future(15), yesterday)

        with patch("data.earnings_calendar._fetch_earnings_date") as mock_fetch:
            mock_fetch.return_value = [{"earnings_date": _future(15), "eps_estimate": 2.0}]
            result = update_earnings_calendar(tmp_db, ["GOOG"], _CONFIG)

        mock_fetch.assert_called_once_with("GOOG")
        assert result["GOOG"] == 1

    def test_mixed_fresh_and_stale(self, tmp_db):
        _insert_fetched(tmp_db, "AAPL", _future(5), _today() + "T08:00:00")

        with patch("data.earnings_calendar._fetch_earnings_date") as mock_fetch:
            mock_fetch.return_value = [{"earnings_date": _future(5), "eps_estimate": 1.0}]
            result = update_earnings_calendar(tmp_db, ["AAPL", "NVDA"], _CONFIG)

        mock_fetch.assert_called_once_with("NVDA")
        assert result["AAPL"] == 0
        assert result["NVDA"] == 1

    def test_all_fetched_today_returns_early(self, tmp_db):
        for ticker, days in [("AAPL", 5), ("MSFT", 12), ("GOOG", 20)]:
            _insert_fetched(tmp_db, ticker, _future(days), _today() + "T07:00:00")

        with patch("data.earnings_calendar._fetch_earnings_date") as mock_fetch:
            result = update_earnings_calendar(tmp_db, ["AAPL", "MSFT", "GOOG"], _CONFIG)

        mock_fetch.assert_not_called()
        assert all(v == 0 for v in result.values())


# ---------------------------------------------------------------------------
# Date window filtering
# ---------------------------------------------------------------------------

class TestDateWindowFiltering:
    def test_past_date_not_stored(self, tmp_db):
        yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
        with patch("data.earnings_calendar._fetch_earnings_date") as mock_fetch:
            mock_fetch.return_value = [{"earnings_date": yesterday, "eps_estimate": None}]
            result = update_earnings_calendar(tmp_db, ["AAPL"], _CONFIG)

        assert result["AAPL"] == 0
        count = tmp_db.execute(sa.select(sa.func.count()).select_from(ec_table)).scalar()
        assert count == 0

    def test_date_beyond_lookahead_not_stored(self, tmp_db):
        far_future = _future(60)
        with patch("data.earnings_calendar._fetch_earnings_date") as mock_fetch:
            mock_fetch.return_value = [{"earnings_date": far_future, "eps_estimate": None}]
            result = update_earnings_calendar(tmp_db, ["AAPL"], _CONFIG)

        assert result["AAPL"] == 0

    def test_date_within_window_stored(self, tmp_db):
        with patch("data.earnings_calendar._fetch_earnings_date") as mock_fetch:
            mock_fetch.return_value = [{"earnings_date": _future(15), "eps_estimate": 2.5}]
            result = update_earnings_calendar(tmp_db, ["AAPL"], _CONFIG)

        assert result["AAPL"] == 1
        row = tmp_db.execute(
            sa.select(ec_table.c.eps_estimate).where(ec_table.c.ticker == "AAPL")
        ).scalar()
        assert row == pytest.approx(2.5)

    def test_no_entries_returned_stores_nothing(self, tmp_db):
        with patch("data.earnings_calendar._fetch_earnings_date", return_value=[]):
            result = update_earnings_calendar(tmp_db, ["AAPL"], _CONFIG)

        assert result["AAPL"] == 0
