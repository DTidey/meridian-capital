"""Earnings calendar — today-skip logic, DB round-trips, dates from .info (packet 41)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import ANY, patch

import pytest
import sqlalchemy as sa

from data._utils import utc_now
from data.db import earnings_calendar as ec_table
from data.earnings_calendar import _fetch_earnings_date, update_earnings_calendar
from data.yahoo_info import YahooInfo, YahooTrend
from tests.yahoo_fake import PGR_TREND, FakeYahoo

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

        mock_fetch.assert_called_once_with("MSFT", ANY, ANY)
        assert result["MSFT"] == 1

    def test_ticker_fetched_yesterday_is_refetched(self, tmp_db):
        yesterday = (utc_now() - timedelta(days=1)).strftime("%Y-%m-%d") + "T12:00:00"
        _insert_fetched(tmp_db, "GOOG", _future(15), yesterday)

        with patch("data.earnings_calendar._fetch_earnings_date") as mock_fetch:
            mock_fetch.return_value = [{"earnings_date": _future(15), "eps_estimate": 2.0}]
            result = update_earnings_calendar(tmp_db, ["GOOG"], _CONFIG)

        mock_fetch.assert_called_once_with("GOOG", ANY, ANY)
        assert result["GOOG"] == 1

    def test_mixed_fresh_and_stale(self, tmp_db):
        _insert_fetched(tmp_db, "AAPL", _future(5), _today() + "T08:00:00")

        with patch("data.earnings_calendar._fetch_earnings_date") as mock_fetch:
            mock_fetch.return_value = [{"earnings_date": _future(5), "eps_estimate": 1.0}]
            result = update_earnings_calendar(tmp_db, ["AAPL", "NVDA"], _CONFIG)

        mock_fetch.assert_called_once_with("NVDA", ANY, ANY)
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


def _ts(days: int, hour: int = 12) -> int:
    """Unix seconds for `hour`:00 UTC, `days` from today."""
    d = (utc_now() + timedelta(days=days)).date()
    return int(datetime(d.year, d.month, d.day, hour, tzinfo=UTC).timestamp())


def _fetch(ticker):
    return _fetch_earnings_date(ticker, YahooInfo(), YahooTrend())


class TestFetchResult:
    def test_failed_request_returns_none(self, monkeypatch, no_waits):
        fake = _yahoo(monkeypatch, info={"AAPL": [_BOOM]})
        assert _fetch("AAPL") is None
        assert fake.calls["info"]["AAPL"] == 3
        assert fake.calls["trend"]["AAPL"] == 0

    def test_empty_success_returns_empty_list(self, monkeypatch, no_waits):
        _yahoo(monkeypatch, info={"AAPL": [{}], "MSFT": [{"forwardEps": 1.0}]})
        assert _fetch("AAPL") == []
        assert _fetch("MSFT") == []

    def test_dates_returned(self, monkeypatch, no_waits):
        info = {"earningsTimestampStart": _ts(10)}
        _yahoo(monkeypatch, info={"AAPL": [_BOOM, info]}, default_trend=PGR_TREND)
        assert _fetch("AAPL") == [{"earnings_date": _future(10), "eps_estimate": 4.11543}]

    def test_empty_counts_as_fetched_no_second_pass(self, tmp_db, no_waits, caplog):
        with (
            patch("data.earnings_calendar._fetch_earnings_date", return_value=[]) as mock_fetch,
            caplog.at_level("INFO"),
        ):
            update_earnings_calendar(tmp_db, ["AAPL"], _CONFIG)
        mock_fetch.assert_called_once_with("AAPL", ANY, ANY)
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
            patch(
                "data.earnings_calendar._fetch_earnings_date",
                side_effect=lambda t, *_: entries.get(t),
            ),
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


# ---------------------------------------------------------------------------
# Packet 41 (AC3): dates from .info, eps_estimate from the earningsTrend 0q average
# ---------------------------------------------------------------------------


def _rows(conn, ticker):
    query = (
        sa.select(ec_table.c.earnings_date, ec_table.c.eps_estimate)
        .where(ec_table.c.ticker == ticker)
        .order_by(ec_table.c.earnings_date)
    )
    return [tuple(r) for r in conn.execute(query)]


class TestInfoDates:
    def test_start_date_with_0q_average(self, tmp_db, monkeypatch, no_waits):
        info = {"earningsTimestampStart": _ts(10), "earningsTimestampEnd": _ts(10, hour=20)}
        fake = _yahoo(monkeypatch, info={"PGR": [info]}, default_trend=PGR_TREND)
        assert update_earnings_calendar(tmp_db, ["PGR"], _CONFIG) == {"PGR": 1}
        assert _rows(tmp_db, "PGR") == [(_future(10), pytest.approx(4.11543))]
        assert fake.calls["calendar"]["PGR"] == 0

    def test_different_end_date_also_stored(self, tmp_db, monkeypatch, no_waits):
        info = {"earningsTimestampStart": _ts(10), "earningsTimestampEnd": _ts(14)}
        _yahoo(monkeypatch, info={"AAPL": [info]}, default_trend=PGR_TREND)
        update_earnings_calendar(tmp_db, ["AAPL"], _CONFIG)
        assert [r[0] for r in _rows(tmp_db, "AAPL")] == [_future(10), _future(14)]

    def test_missing_timestamps_success_no_dates(self, tmp_db, monkeypatch, no_waits, caplog):
        fake = _yahoo(monkeypatch, info={"AAPL": [{"forwardEps": 7.5}]})
        with caplog.at_level("INFO"):
            assert update_earnings_calendar(tmp_db, ["AAPL"], _CONFIG) == {"AAPL": 0}
        assert "Earnings calendar: 1/1 tickers fetched" in caplog.text
        assert fake.calls["info"]["AAPL"] == 1

    def test_failed_info_is_failed_fetch(self, tmp_db, monkeypatch, no_waits, caplog):
        fake = _yahoo(monkeypatch, info={"AAPL": [_BOOM]}, default_trend=PGR_TREND)
        with caplog.at_level("INFO"):
            update_earnings_calendar(tmp_db, ["AAPL"], _CONFIG)
        assert "Earnings calendar: 0/1 tickers fetched after retries (below 95%)" in caplog.text
        assert fake.calls["info"]["AAPL"] == 6  # both passes
        assert fake.calls["trend"]["AAPL"] == 0

    def test_trend_failure_stores_null_eps(self, tmp_db, monkeypatch, no_waits, caplog):
        info = {"earningsTimestampStart": _ts(10)}
        _yahoo(monkeypatch, info={"AAPL": [info]}, trend={"AAPL": [_BOOM]})
        with caplog.at_level("INFO"):
            assert update_earnings_calendar(tmp_db, ["AAPL"], _CONFIG) == {"AAPL": 1}
        assert _rows(tmp_db, "AAPL") == [(_future(10), None)]
        assert "Earnings calendar: 1/1 tickers fetched" in caplog.text

    def test_no_0q_row_stores_null_eps(self, tmp_db, monkeypatch, no_waits):
        info = {"earningsTimestampStart": _ts(10)}
        plus_1y_only = {"+1y": PGR_TREND["+1y"]}
        _yahoo(monkeypatch, info={"AAPL": [info]}, default_trend=plus_1y_only)
        update_earnings_calendar(tmp_db, ["AAPL"], _CONFIG)
        assert _rows(tmp_db, "AAPL") == [(_future(10), None)]

    def test_reuses_the_runs_info_and_trend(self, tmp_db, monkeypatch, no_waits):
        info_dict = {"earningsTimestampStart": _ts(10)}
        fake = _yahoo(monkeypatch, default_info=info_dict, default_trend=PGR_TREND)
        info, trend = YahooInfo(), YahooTrend()
        for t in ("AAPL", "MSFT"):
            info.get(t)
            trend.get(t)
        update_earnings_calendar(tmp_db, ["AAPL", "MSFT"], _CONFIG, info=info, trend=trend)
        assert fake.calls["info"] == {"AAPL": 1, "MSFT": 1}
        assert fake.calls["trend"] == {"AAPL": 1, "MSFT": 1}
        assert sum(fake.calls["calendar"].values()) == 0

    def test_standalone_fetches_its_own(self, tmp_db, monkeypatch, no_waits):
        info_dict = {"earningsTimestampStart": _ts(10)}
        fake = _yahoo(monkeypatch, default_info=info_dict, default_trend=PGR_TREND)
        assert update_earnings_calendar(tmp_db, ["AAPL"], _CONFIG) == {"AAPL": 1}
        assert fake.calls["info"]["AAPL"] == 1
        assert fake.calls["trend"]["AAPL"] == 1

    def test_superseded_rule_still_applies(self, tmp_db, monkeypatch, no_waits):
        past = (utc_now() - timedelta(days=20)).strftime("%Y-%m-%d")
        for ed in (past, _future(8)):
            _insert_fetched(tmp_db, "AAPL", ed, _yesterday_ts())
        _yahoo(monkeypatch, default_info={"earningsTimestampStart": _ts(12)})
        update_earnings_calendar(tmp_db, ["AAPL"], _CONFIG)
        assert _dates(tmp_db, "AAPL") == [past, _future(12)]

    # Adversarial: earningsTimestamp is the previous report; it is never stored.
    def test_previous_report_timestamp_ignored(self, tmp_db, monkeypatch, no_waits):
        _yahoo(monkeypatch, default_info={"earningsTimestamp": _ts(5)})
        assert update_earnings_calendar(tmp_db, ["AAPL"], _CONFIG) == {"AAPL": 0}
        assert _rows(tmp_db, "AAPL") == []

    # Adversarial: the date is the UTC date whatever the machine's local time zone.
    def test_timestamp_converted_in_utc(self, tmp_db, monkeypatch, no_waits):
        import time

        monkeypatch.setenv("TZ", "America/New_York")
        time.tzset()
        try:
            _yahoo(monkeypatch, default_info={"earningsTimestampStart": _ts(10, hour=1)})
            update_earnings_calendar(tmp_db, ["AAPL"], _CONFIG)
        finally:
            monkeypatch.undo()
            time.tzset()
        assert [r[0] for r in _rows(tmp_db, "AAPL")] == [_future(10)]

    # Adversarial: a malformed timestamp is no date, not a crash or a failed fetch.
    def test_malformed_timestamp_no_date(self, tmp_db, monkeypatch, no_waits, caplog):
        _yahoo(monkeypatch, default_info={"earningsTimestampStart": "soon"})
        with caplog.at_level("INFO"):
            assert update_earnings_calendar(tmp_db, ["AAPL"], _CONFIG) == {"AAPL": 0}
        assert "Earnings calendar: 1/1 tickers fetched" in caplog.text

    # Adversarial: an end timestamp without a start is not a date (spec: start, plus a
    # different end).
    def test_end_without_start_no_date(self, tmp_db, monkeypatch, no_waits):
        _yahoo(monkeypatch, default_info={"earningsTimestampEnd": _ts(10)})
        assert update_earnings_calendar(tmp_db, ["AAPL"], _CONFIG) == {"AAPL": 0}
