"""Earnings calendar — today-skip logic and DB round-trips."""

from datetime import timedelta
from unittest.mock import patch

import pytest
import sqlalchemy as sa

from data._utils import utc_now
from data.db import earnings_calendar as ec_table
from data.earnings_calendar import _fetch_earnings_date, update_earnings_calendar
from tests.yahoo_fake import FakeYahoo

_CONFIG = {"earnings_calendar": {"lookahead_days": 30}}


def _today() -> str:
    return utc_now().strftime("%Y-%m-%d")


def _future(days: int) -> str:
    return (utc_now() + timedelta(days=days)).strftime("%Y-%m-%d")


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
        yesterday = (utc_now() - timedelta(days=1)).strftime("%Y-%m-%d") + "T12:00:00"
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
        yesterday = (utc_now() - timedelta(days=1)).strftime("%Y-%m-%d")
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


# ---------------------------------------------------------------------------
# Packet 40: failed fetch vs empty result (AC4), superseded dates (AC5)
# ---------------------------------------------------------------------------

_BOOM = RuntimeError("Too Many Requests")


@pytest.fixture
def no_waits(monkeypatch):
    monkeypatch.setattr("data.yahoo_info.RETRY_DELAYS", (0, 0))
    monkeypatch.setattr("data.yahoo_info.SECOND_PASS_PAUSE", 0)


def _yahoo(monkeypatch, **kwargs):
    fake = FakeYahoo(**kwargs)
    monkeypatch.setattr("yfinance.Ticker", fake)
    return fake


def _dates(conn, ticker):
    rows = conn.execute(
        sa.select(ec_table.c.earnings_date)
        .where(ec_table.c.ticker == ticker)
        .order_by(ec_table.c.earnings_date)
    )
    return list(rows.scalars())


def _yesterday_ts() -> str:
    return (utc_now() - timedelta(days=1)).strftime("%Y-%m-%d") + "T12:00:00"


class TestFetchResult:
    def test_failed_request_returns_none(self, monkeypatch, no_waits):
        fake = _yahoo(monkeypatch, calendar={"AAPL": [_BOOM]})
        assert _fetch_earnings_date("AAPL") is None
        assert fake.calls["calendar"]["AAPL"] == 3

    def test_empty_success_returns_empty_list(self, monkeypatch, no_waits):
        _yahoo(monkeypatch, calendar={"AAPL": [{}], "MSFT": [None]})
        assert _fetch_earnings_date("AAPL") == []
        assert _fetch_earnings_date("MSFT") == []

    def test_dates_returned(self, monkeypatch, no_waits):
        cal = {"Earnings Date": [_future(10)], "Earnings Average": [1.5]}
        _yahoo(monkeypatch, calendar={"AAPL": [_BOOM, cal]})
        assert _fetch_earnings_date("AAPL") == [{"earnings_date": _future(10), "eps_estimate": 1.5}]

    def test_empty_counts_as_fetched_no_second_pass(self, tmp_db, no_waits, caplog):
        with (
            patch("data.earnings_calendar._fetch_earnings_date", return_value=[]) as mock_fetch,
            caplog.at_level("INFO"),
        ):
            update_earnings_calendar(tmp_db, ["AAPL"], _CONFIG)
        mock_fetch.assert_called_once_with("AAPL")
        assert "Earnings calendar: 1/1 tickers fetched" in caplog.text
        assert "ERROR" not in [r.levelname for r in caplog.records]

    def test_none_counts_against_coverage_and_retries(self, tmp_db, no_waits, caplog):
        with (
            patch("data.earnings_calendar._fetch_earnings_date", return_value=None) as mock_fetch,
            caplog.at_level("INFO"),
        ):
            result = update_earnings_calendar(tmp_db, ["AAPL"], _CONFIG)
        assert mock_fetch.call_count == 2
        assert result["AAPL"] == 0
        assert "Earnings calendar: 0/1 tickers fetched after retries (below 95%)" in caplog.text


class TestSupersededDates:
    def _seed(self, conn):
        past = (utc_now() - timedelta(days=20)).strftime("%Y-%m-%d")
        for ticker, ed in [
            ("AAPL", past),
            ("AAPL", _future(8)),
            ("AAPL", _future(12)),
            ("MSFT", _future(9)),
        ]:
            _insert_fetched(conn, ticker, ed, _yesterday_ts())
        return past

    def test_success_with_dates_deletes_other_future_rows(self, tmp_db, no_waits, caplog):
        past = self._seed(tmp_db)
        entries = {"AAPL": [{"earnings_date": _future(12), "eps_estimate": 1.0}], "MSFT": None}
        with (
            patch("data.earnings_calendar._fetch_earnings_date", side_effect=entries.get),
            caplog.at_level("INFO"),
        ):
            update_earnings_calendar(tmp_db, ["AAPL", "MSFT"], _CONFIG)
        assert _dates(tmp_db, "AAPL") == [past, _future(12)]
        assert _dates(tmp_db, "MSFT") == [_future(9)]
        assert "1 upcoming events stored, 1 superseded removed" in caplog.text

    @pytest.mark.parametrize("fetched", [None, []])
    def test_failed_or_empty_fetch_keeps_rows(self, tmp_db, no_waits, fetched):
        past = self._seed(tmp_db)
        with patch("data.earnings_calendar._fetch_earnings_date", return_value=fetched):
            update_earnings_calendar(tmp_db, ["AAPL"], _CONFIG)
        assert _dates(tmp_db, "AAPL") == [past, _future(8), _future(12)]

    def test_only_out_of_window_dates_keeps_rows(self, tmp_db, no_waits):
        past = self._seed(tmp_db)
        entries = [{"earnings_date": _future(60), "eps_estimate": None}]
        with patch("data.earnings_calendar._fetch_earnings_date", return_value=entries):
            update_earnings_calendar(tmp_db, ["AAPL"], _CONFIG)
        assert _dates(tmp_db, "AAPL") == [past, _future(8), _future(12)]

    # Adversarial: a stored future date beyond the lookahead window is also superseded.
    def test_beyond_window_future_row_superseded(self, tmp_db, no_waits):
        _insert_fetched(tmp_db, "AAPL", _future(45), _yesterday_ts())
        entries = [{"earnings_date": _future(10), "eps_estimate": None}]
        with patch("data.earnings_calendar._fetch_earnings_date", return_value=entries):
            update_earnings_calendar(tmp_db, ["AAPL"], _CONFIG)
        assert _dates(tmp_db, "AAPL") == [_future(10)]

    # Adversarial: today's date counts as future; it is kept when re-confirmed.
    def test_two_dates_kept_when_both_returned(self, tmp_db, no_waits):
        _insert_fetched(tmp_db, "AAPL", _today(), _yesterday_ts())
        _insert_fetched(tmp_db, "AAPL", _future(5), _yesterday_ts())
        entries = [
            {"earnings_date": _today(), "eps_estimate": None},
            {"earnings_date": _future(5), "eps_estimate": None},
        ]
        with patch("data.earnings_calendar._fetch_earnings_date", return_value=entries):
            update_earnings_calendar(tmp_db, ["AAPL"], _CONFIG)
        assert _dates(tmp_db, "AAPL") == [_today(), _future(5)]
