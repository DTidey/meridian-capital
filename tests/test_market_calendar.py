"""execution/market_calendar.py: is the score date a US market trading day (packet 68)."""

import socket
import time
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
import requests

from execution import market_calendar
from execution.market_calendar import CalendarUnavailable, closed_reason

THANKSGIVING = date(2026, 11, 26)
BLACK_FRIDAY = date(2026, 11, 27)  # early close 13:00 ET
MONDAY = date(2026, 11, 30)


def entry(day: date, close_hour_utc: int = 21):
    """A calendar entry shaped like alpaca.trading.models.Calendar."""
    return SimpleNamespace(
        date=day,
        open=datetime(day.year, day.month, day.day, 14, 30, tzinfo=UTC),
        close=datetime(day.year, day.month, day.day, close_hour_utc, 0, tzinfo=UTC),
    )


class FakeClient:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = [] if response is None else response
        self.error = error
        self.requests = []
        self._session = requests.Session()  # as alpaca-py's RESTClient has

    def get_calendar(self, filters=None):
        self.requests.append(filters)
        if self.error:
            raise self.error
        return self.response


def factory(client):
    return lambda: client


def test_no_entry_is_holiday():
    client = FakeClient([])
    assert closed_reason(THANKSGIVING, factory(client)) == "holiday"
    req = client.requests[0]
    assert (req.start, req.end) == (THANKSGIVING, THANKSGIVING)


def test_trading_day_is_open():
    assert closed_reason(MONDAY, factory(FakeClient([entry(MONDAY)]))) is None


def test_half_day_is_open():
    client = FakeClient([entry(BLACK_FRIDAY, close_hour_utc=18)])  # 13:00 ET
    assert closed_reason(BLACK_FRIDAY, factory(client)) is None


@pytest.mark.parametrize("day", [date(2026, 11, 28), date(2026, 11, 29)])
def test_weekend_needs_no_client(day):
    def no_client():
        raise AssertionError("no Alpaca client on a weekend")

    assert closed_reason(day, no_client) == "weekend"


def test_call_error_is_unavailable():
    client = FakeClient(error=ConnectionError("timed out"))
    with pytest.raises(CalendarUnavailable, match="timed out"):
        closed_reason(MONDAY, factory(client))


def test_missing_keys_is_unavailable():
    def no_keys():
        raise OSError("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in environment.")

    with pytest.raises(CalendarUnavailable, match="ALPACA_API_KEY"):
        closed_reason(MONDAY, no_keys)


def test_default_factory_is_broker_get_client(monkeypatch):
    client = FakeClient([entry(MONDAY)])
    monkeypatch.setattr(market_calendar, "get_client", factory(client))
    assert closed_reason(MONDAY) is None
    assert len(client.requests) == 1


# Adversarial: unusable responses must fail open (raise), never read as "holiday".


def test_only_other_dates_is_unavailable():
    client = FakeClient([entry(date(2026, 11, 25)), entry(BLACK_FRIDAY)])
    with pytest.raises(CalendarUnavailable):
        closed_reason(THANKSGIVING, factory(client))


def test_raw_dict_response_is_unavailable():
    client = FakeClient({"date": "2026-11-30", "open": "09:30", "close": "16:00"})
    with pytest.raises(CalendarUnavailable):
        closed_reason(MONDAY, factory(client))


def test_entry_without_date_is_unavailable():
    client = FakeClient([SimpleNamespace(open="09:30", close="16:00")])
    with pytest.raises(CalendarUnavailable):
        closed_reason(MONDAY, factory(client))


def test_none_response_is_unavailable():
    client = FakeClient()
    client.response = None
    with pytest.raises(CalendarUnavailable):
        closed_reason(MONDAY, factory(client))


# Timeouts: alpaca-py passes no timeout to requests, so a stalled connection would hang.


@pytest.fixture
def stalled_server():
    """A localhost socket that accepts connections (via the backlog) and never answers."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    yield f"http://127.0.0.1:{srv.getsockname()[1]}"
    srv.close()


def real_client_factory(url: str):
    from alpaca.trading.client import TradingClient

    return lambda: TradingClient("key", "secret", paper=True, url_override=url)


def test_timeout_is_finite_and_short():
    connect, read = market_calendar.CALENDAR_TIMEOUT
    assert 0 < connect <= 10 and 0 < read <= 30


def test_dedicated_session_carries_the_timeout():
    client = FakeClient([entry(MONDAY)])
    closed_reason(MONDAY, factory(client))
    assert isinstance(client._session, market_calendar._TimeoutSession)
    assert client._session.timeout == market_calendar.CALENDAR_TIMEOUT


def test_session_passes_timeout_to_requests(monkeypatch):
    seen = {}

    def fake_request(self, method, url, **kwargs):
        seen.update(kwargs)
        return "response"

    monkeypatch.setattr(requests.Session, "request", fake_request)
    session = market_calendar._TimeoutSession((1.0, 2.0))
    assert session.request("GET", "http://example.invalid/v2/calendar") == "response"
    assert seen["timeout"] == (1.0, 2.0)


def test_real_sdk_client_uses_the_timeout(monkeypatch):
    """The pinned alpaca-py routes get_calendar through client._session.request."""
    seen = {}

    def fake_request(self, method, url, **kwargs):
        seen.update(kwargs, url=url)
        resp = requests.Response()
        resp.status_code = 200
        resp._content = b'[{"date": "2026-11-30", "open": "09:30", "close": "16:00"}]'
        return resp

    monkeypatch.setattr(requests.Session, "request", fake_request)
    assert closed_reason(MONDAY, real_client_factory("http://127.0.0.1:9")) is None
    assert seen["timeout"] == market_calendar.CALENDAR_TIMEOUT
    assert seen["url"].endswith("/v2/calendar")


def test_stalled_calendar_times_out(monkeypatch, stalled_server):
    monkeypatch.setattr(market_calendar, "CALENDAR_TIMEOUT", (0.5, 0.5))
    t0 = time.monotonic()
    with pytest.raises(CalendarUnavailable, match="Timeout"):
        closed_reason(MONDAY, real_client_factory(stalled_server))
    assert time.monotonic() - t0 < 5


def test_client_without_session_is_unavailable():
    client = FakeClient([entry(MONDAY)])
    del client._session
    with pytest.raises(CalendarUnavailable, match="timeout"):
        closed_reason(MONDAY, factory(client))
    assert client.requests == []  # never called without a bound
