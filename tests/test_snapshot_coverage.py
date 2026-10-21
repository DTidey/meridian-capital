"""Snapshot steps: second pass over failed tickers (AC3) and coverage ERROR below 95% (AC6)."""

from datetime import timedelta

import pytest
import sqlalchemy as sa

import data.yahoo_info as yahoo_info
from data._utils import utc_now
from data.db import analyst_estimates, earnings_calendar, short_interest
from data.earnings_calendar import update_earnings_calendar
from data.estimates import update_estimates
from data.short_interest import update_short_interest
from tests.yahoo_fake import INFO, FakeYahoo

_CONFIG = {"earnings_calendar": {"lookahead_days": 30}}
_BOOM = RuntimeError("Too Many Requests")


def _future(days):
    return (utc_now() + timedelta(days=days)).strftime("%Y-%m-%d")


def _calendar():
    return {"Earnings Date": [_future(10)], "Earnings Average": [1.5]}


@pytest.fixture
def sleeps(monkeypatch):
    recorded = []
    monkeypatch.setattr(yahoo_info.time, "sleep", recorded.append)
    return recorded


@pytest.fixture
def fake(monkeypatch):
    def install(**kwargs):
        f = FakeYahoo(**kwargs)
        monkeypatch.setattr("yfinance.Ticker", f)
        return f

    return install


def _run_info_step(step, conn, tickers):
    if step == "short_interest":
        return update_short_interest(conn, tickers)
    if step == "estimates":
        return update_estimates(conn, tickers)
    return update_earnings_calendar(conn, tickers, _CONFIG)


def _rows(conn, table, ticker):
    query = sa.select(sa.func.count()).select_from(table).where(table.c.ticker == ticker)
    return conn.execute(query).scalar()


class TestSecondPass:
    @pytest.mark.parametrize(
        "step, table",
        [("short_interest", short_interest), ("estimates", analyst_estimates)],
    )
    def test_info_step_second_pass_success_stored(self, tmp_db, fake, sleeps, step, table):
        f = fake(info={"AAPL": [_BOOM, _BOOM, _BOOM, INFO]}, default_info=INFO)
        summary = _run_info_step(step, tmp_db, ["AAPL", "MSFT"])
        assert summary == {"AAPL": True, "MSFT": True}
        assert f.calls["info"] == {"AAPL": 4, "MSFT": 1}
        assert _rows(tmp_db, table, "AAPL") == 1
        assert sleeps == [5, 15, 60]

    def test_calendar_second_pass_success_stored(self, tmp_db, fake, sleeps):
        f = fake(
            calendar={"AAPL": [_BOOM, _BOOM, _BOOM, _calendar()]}, default_calendar=_calendar()
        )
        summary = update_earnings_calendar(tmp_db, ["AAPL", "MSFT"], _CONFIG)
        assert summary == {"AAPL": 1, "MSFT": 1}
        assert f.calls["calendar"] == {"AAPL": 4, "MSFT": 1}
        assert _rows(tmp_db, earnings_calendar, "AAPL") == 1
        assert sleeps == [5, 15, 60]

    # Adversarial: no failures means no 60-second pause and no extra requests.
    def test_no_pause_without_failures(self, tmp_db, fake, sleeps):
        f = fake(default_info=INFO)
        update_short_interest(tmp_db, ["AAPL", "MSFT"])
        assert sleeps == []
        assert f.calls["info"] == {"AAPL": 1, "MSFT": 1}

    # Adversarial: a ticker failing both passes is tried exactly twice (3 attempts each).
    def test_fail_both_passes(self, tmp_db, fake, sleeps):
        f = fake(info={"AAPL": [_BOOM]}, default_info=INFO)
        summary = update_estimates(tmp_db, ["AAPL", "MSFT"])
        assert summary == {"AAPL": False, "MSFT": True}
        assert f.calls["info"]["AAPL"] == 6

    # Adversarial: .info without the step's fields is a success with no row, not a failure.
    def test_missing_fields_not_retried(self, tmp_db, fake, sleeps):
        f = fake(default_info={"longName": "Apple"})
        summary = update_short_interest(tmp_db, ["AAPL"])
        assert summary == {"AAPL": False}
        assert f.calls["info"]["AAPL"] == 1
        assert sleeps == []


class TestCoverageError:
    @pytest.mark.parametrize(
        "step, label",
        [
            ("short_interest", "Short interest"),
            ("estimates", "Analyst estimates"),
            ("calendar", "Earnings calendar"),
        ],
    )
    def test_below_95_logs_error_and_continues(self, tmp_db, fake, sleeps, caplog, step, label):
        fake(
            info={"AAPL": [_BOOM]},
            calendar={"AAPL": [_BOOM]},
            default_info=INFO,
            default_calendar=_calendar(),
        )
        with caplog.at_level("INFO"):
            summary = _run_info_step(step, tmp_db, ["AAPL", "MSFT"])
        assert summary["MSFT"]
        errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
        assert errors == [f"{label}: 1/2 tickers fetched after retries (below 95%)"]
        assert f"{label}: 1/2 tickers fetched" in caplog.text

    def test_exactly_95_no_error(self, tmp_db, fake, sleeps, caplog):
        tickers = [f"T{i:02d}" for i in range(20)]
        fake(info={"T00": [_BOOM]}, default_info=INFO)
        with caplog.at_level("INFO"):
            update_short_interest(tmp_db, tickers)
        assert "Short interest: 19/20 tickers fetched" in caplog.text
        assert not [r for r in caplog.records if r.levelname == "ERROR"]

    def test_all_fail_logs_error(self, tmp_db, fake, sleeps, caplog):
        fake(calendar={"AAPL": [_BOOM], "MSFT": [_BOOM]})
        with caplog.at_level("INFO"):
            summary = update_earnings_calendar(tmp_db, ["AAPL", "MSFT"], _CONFIG)
        assert summary == {"AAPL": 0, "MSFT": 0}
        assert "Earnings calendar: 0/2 tickers fetched after retries (below 95%)" in caplog.text

    def test_skipped_today_not_attempted(self, tmp_db, fake, sleeps, caplog):
        today = utc_now().strftime("%Y-%m-%d")
        tmp_db.execute(short_interest.insert().values(ticker="AAPL", date=today, shares_short=1.0))
        tmp_db.commit()
        f = fake(default_info=INFO)
        with caplog.at_level("INFO"):
            update_short_interest(tmp_db, ["AAPL", "MSFT"])
        assert "Short interest: 1/1 tickers fetched" in caplog.text
        assert f.calls["info"] == {"MSFT": 1}

    # Adversarial: nothing to attempt (all snapshotted today) is not low coverage.
    def test_nothing_attempted_no_error(self, tmp_db, fake, sleeps, caplog):
        today = utc_now().strftime("%Y-%m-%d")
        tmp_db.execute(
            analyst_estimates.insert().values(ticker="AAPL", date=today, eps_estimate_fwd=1.0)
        )
        tmp_db.commit()
        fake(default_info=INFO)
        with caplog.at_level("INFO"):
            update_estimates(tmp_db, ["AAPL"])
        assert "Analyst estimates: 0/0 tickers fetched" in caplog.text
        assert not [r for r in caplog.records if r.levelname == "ERROR"]
