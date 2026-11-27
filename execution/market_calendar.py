"""Is a date a US equity trading day? Weekends locally, holidays from Alpaca's calendar."""

from __future__ import annotations

from datetime import date

from execution.broker import get_client


class CalendarUnavailable(Exception):
    """The calendar lookup failed; callers must run normally, never skip."""


def closed_reason(day: date, client_factory=None) -> str | None:
    """'weekend' or 'holiday' when the market is closed on day; None on a trading day.

    Half-days are trading days. Raises CalendarUnavailable on any lookup failure or an
    unusable response, so an error can never be read as a holiday.
    """
    if day.weekday() >= 5:
        return "weekend"
    try:
        from alpaca.trading.requests import GetCalendarRequest

        client = (client_factory or get_client)()
        entries = client.get_calendar(GetCalendarRequest(start=day, end=day))
        listed = [e.date for e in entries]
    except Exception as exc:
        raise CalendarUnavailable(f"{type(exc).__name__}: {exc}") from exc
    if not listed:
        return "holiday"
    if day in listed:
        return None
    raise CalendarUnavailable(f"calendar for {day} listed only {sorted(map(str, listed))}")
