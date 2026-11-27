"""Is a date a US equity trading day? Weekends locally, holidays from Alpaca's calendar."""

from __future__ import annotations

from datetime import date, datetime

import requests

from execution.broker import get_client

# (connect, read) seconds for the calendar call; alpaca-py sets no timeout of its own.
CALENDAR_TIMEOUT = (5.0, 10.0)


class CalendarUnavailable(Exception):
    """The calendar lookup failed; callers must run normally, never skip."""


class _TimeoutSession(requests.Session):
    """A requests Session that applies a default timeout to every request."""

    def __init__(self, timeout: tuple[float, float]):
        super().__init__()
        self.timeout = timeout

    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", self.timeout)
        return super().request(method, url, **kwargs)


def _with_timeout(client):
    """Give this dedicated client a timeout session; refuse a client we cannot bound."""
    if not isinstance(getattr(client, "_session", None), requests.Session):
        raise CalendarUnavailable("cannot set a timeout on the Alpaca client (no _session)")
    client._session = _TimeoutSession(CALENDAR_TIMEOUT)
    return client


def _listed_dates(entries) -> list[date]:
    """Dates in a calendar response; anything but a list/tuple of dated entries is unusable.

    Checked before emptiness is read as a holiday, so a malformed answer can never skip a day.
    """
    if not isinstance(entries, list | tuple):
        raise CalendarUnavailable(f"unusable calendar response: {type(entries).__name__}")
    listed = [getattr(e, "date", None) for e in entries]
    for d in listed:
        # datetime is a date subclass; a calendar day must be a plain date
        if not isinstance(d, date) or isinstance(d, datetime):
            raise CalendarUnavailable(f"unusable calendar entry date: {d!r}")
    return listed


def closed_reason(day: date, client_factory=None) -> str | None:
    """'weekend' or 'holiday' when the market is closed on day; None on a trading day.

    Half-days are trading days. Raises CalendarUnavailable on any lookup failure, timeout or
    unusable response, so an error can never be read as a holiday.
    """
    if day.weekday() >= 5:
        return "weekend"
    try:
        from alpaca.trading.requests import GetCalendarRequest

        client = _with_timeout((client_factory or get_client)())
        entries = client.get_calendar(GetCalendarRequest(start=day, end=day))
        listed = _listed_dates(entries)
    except CalendarUnavailable:
        raise
    except Exception as exc:
        raise CalendarUnavailable(f"{type(exc).__name__}: {exc}") from exc
    if not listed:
        return "holiday"
    if day in listed:
        return None
    raise CalendarUnavailable(f"calendar for {day} listed only {sorted(map(str, listed))}")
