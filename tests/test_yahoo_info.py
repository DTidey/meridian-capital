"""Shared Yahoo .info fetch: retry with backoff (AC1) and one request per ticker per run (AC2)."""

import pytest
import sqlalchemy as sa
from yfinance.exceptions import YFRateLimitError

import data.yahoo_info as yahoo_info
from data.db import analyst_estimates, short_interest
from data.estimates import update_estimates
from data.short_interest import update_short_interest
from data.yahoo_info import YahooInfo
from tests.yahoo_fake import INFO, FakeYahoo


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
