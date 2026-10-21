"""Shared Yahoo .info and earningsTrend fetches: retry with backoff and one request per ticker
per run (packet 40 AC1, AC2; packet 41 AC1)."""

import pytest
import sqlalchemy as sa
from yfinance.exceptions import YFRateLimitError

import data.yahoo_info as yahoo_info
from data.db import analyst_estimates, short_interest
from data.estimates import update_estimates
from data.short_interest import update_short_interest
from data.yahoo_info import YahooInfo, YahooTrend
from tests.yahoo_fake import INFO, PGR_TREND, FakeYahoo, trend


@pytest.fixture
def sleeps(monkeypatch):
    recorded = []
    monkeypatch.setattr(yahoo_info.time, "sleep", recorded.append)
    return recorded


def _install(monkeypatch, fake):
    monkeypatch.setattr(yahoo_info.yf, "Ticker", fake)
    return fake


class TestRetry:
    def test_raise_then_succeed_returns_data(self, monkeypatch, sleeps):
        fake = _install(monkeypatch, FakeYahoo(info={"AAPL": [RuntimeError("429"), INFO]}))
        assert YahooInfo().get("AAPL") == INFO
        assert fake.calls["info"]["AAPL"] == 2
        assert sleeps == [5]

    def test_waits_increase(self, monkeypatch, sleeps):
        boom = RuntimeError("throttled")
        _install(monkeypatch, FakeYahoo(info={"AAPL": [boom, boom, INFO]}))
        assert YahooInfo().get("AAPL") == INFO
        assert sleeps == [5, 15]

    def test_three_failures_count_as_failed(self, monkeypatch, sleeps):
        fake = _install(monkeypatch, FakeYahoo(info={"AAPL": [RuntimeError("down")]}))
        assert YahooInfo().get("AAPL") is None
        assert fake.calls["info"]["AAPL"] == 3
        assert sleeps == [5, 15]

    def test_rate_limit_error_retried(self, monkeypatch, sleeps):
        _install(monkeypatch, FakeYahoo(info={"AAPL": [YFRateLimitError(), INFO]}))
        assert YahooInfo().get("AAPL") == INFO

    # Adversarial: an empty .info is a successful fetch, not a failure to retry.
    def test_empty_info_is_success(self, monkeypatch, sleeps):
        fake = _install(monkeypatch, FakeYahoo(info={"AAPL": [{}]}))
        assert YahooInfo().get("AAPL") == {}
        assert fake.calls["info"]["AAPL"] == 1
        assert sleeps == []

    # Adversarial: a failure is not memoised, so a later step can still recover the ticker.
    def test_failure_not_cached(self, monkeypatch, sleeps):
        boom = RuntimeError("down")
        fake = _install(monkeypatch, FakeYahoo(info={"AAPL": [boom, boom, boom, INFO]}))
        info = YahooInfo()
        assert info.get("AAPL") is None
        assert info.get("AAPL") == INFO
        assert fake.calls["info"]["AAPL"] == 4


class TestSharedInfo:
    def test_repeat_get_makes_no_request(self, monkeypatch, sleeps):
        fake = _install(monkeypatch, FakeYahoo(default_info=INFO))
        info = YahooInfo()
        info.get("AAPL")
        info.get("AAPL")
        assert fake.calls["info"]["AAPL"] == 1

    def test_short_interest_then_estimates_one_request_each(self, tmp_db, monkeypatch, sleeps):
        fake = _install(monkeypatch, FakeYahoo(default_info=INFO))
        info = YahooInfo()
        update_short_interest(tmp_db, ["AAPL", "MSFT"], info=info)
        est = update_estimates(tmp_db, ["AAPL", "MSFT"], info=info)
        assert est == {"AAPL": True, "MSFT": True}
        assert fake.calls["info"] == {"AAPL": 1, "MSFT": 1}
        for table in (short_interest, analyst_estimates):
            count = tmp_db.execute(sa.select(sa.func.count()).select_from(table)).scalar()
            assert count == 2

    def test_estimates_log_shared_count(self, tmp_db, monkeypatch, sleeps, caplog):
        _install(monkeypatch, FakeYahoo(default_info=INFO))
        info = YahooInfo()
        update_short_interest(tmp_db, ["AAPL"], info=info)
        with caplog.at_level("INFO"):
            update_estimates(tmp_db, ["AAPL", "MSFT"], info=info)
        assert "Analyst estimates: 2/2 tickers fetched (1 from the shared .info)" in caplog.text

    def test_standalone_callers_get_own_info(self, tmp_db, monkeypatch, sleeps):
        fake = _install(monkeypatch, FakeYahoo(default_info=INFO))
        assert update_short_interest(tmp_db, ["AAPL"]) == {"AAPL": True}
        assert update_estimates(tmp_db, ["AAPL"]) == {"AAPL": True}
        assert fake.calls["info"]["AAPL"] == 2


class TestSharedTrend:
    def test_one_request_serves_trend_and_quarter_estimate(self, monkeypatch, sleeps):
        fake = _install(monkeypatch, FakeYahoo(trend={"PGR": [PGR_TREND]}))
        got = YahooTrend().get("PGR")
        assert got["+1y"] == {
            "current": pytest.approx(16.30119),
            "days_7": pytest.approx(16.36848),
            "days_30": pytest.approx(16.27934),
            "days_60": pytest.approx(16.0),
            "days_90": pytest.approx(15.8),
            "avg": pytest.approx(16.30119),
        }
        assert got["0q"]["avg"] == pytest.approx(4.11543)
        assert fake.calls["trend"]["PGR"] == 1

    def test_repeat_get_makes_no_request(self, monkeypatch, sleeps):
        fake = _install(monkeypatch, FakeYahoo(default_trend=PGR_TREND))
        tr = YahooTrend()
        assert tr.get("PGR") == tr.get("PGR")
        assert fake.calls["trend"]["PGR"] == 1

    def test_raise_then_succeed_retried(self, monkeypatch, sleeps):
        fake = _install(monkeypatch, FakeYahoo(trend={"PGR": [RuntimeError("429"), PGR_TREND]}))
        assert YahooTrend().get("PGR")["+1y"]["current"] == pytest.approx(16.30119)
        assert fake.calls["trend"]["PGR"] == 2
        assert sleeps == [5]

    def test_three_failures_none_and_not_cached(self, monkeypatch, sleeps):
        boom = YFRateLimitError()
        fake = _install(monkeypatch, FakeYahoo(trend={"PGR": [boom, boom, boom, PGR_TREND]}))
        tr = YahooTrend()
        assert tr.get("PGR") is None
        assert sleeps == [5, 15]
        assert tr.get("PGR")["+1y"]["current"] == pytest.approx(16.30119)
        assert fake.calls["trend"]["PGR"] == 4

    def test_no_coverage_is_empty_success(self, monkeypatch, sleeps):
        fake = _install(monkeypatch, FakeYahoo(trend={"NEW": [{}]}))
        assert YahooTrend().get("NEW") == {}
        assert fake.calls["trend"]["NEW"] == 1
        assert sleeps == []

    # Adversarial: a value Yahoo leaves out is None, not NaN, and the currency is not a column.
    def test_missing_value_is_none(self, monkeypatch, sleeps):
        _install(monkeypatch, FakeYahoo(default_trend={"+1y": trend(2.0, d30=1.9), "0q": {}}))
        got = YahooTrend().get("X")
        assert got["+1y"]["days_7"] is None
        assert got["+1y"]["days_30"] == pytest.approx(1.9)
        assert "currency" not in got["+1y"]
