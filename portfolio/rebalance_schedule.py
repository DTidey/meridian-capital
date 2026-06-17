"""Advisory rebalance schedule checks — never blocks trading."""

import logging
from datetime import date, timedelta

import sqlalchemy as sa

from data.db import earnings_calendar

logger = logging.getLogger(__name__)

# Hardcoded 2026 FOMC meeting dates
_FOMC_2026 = [
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-16",
]

_EARNINGS_WARN_DAYS = 2
_FOMC_WARN_DAYS     = 5
_OPEX_WARN_DAYS     = 3


def check_events(
    tickers: list[str],
    score_date: str,
    conn: sa.engine.Connection,
    config: dict | None = None,
) -> list[str]:
    """Return list of advisory warning strings (may be empty)."""
    d = date.fromisoformat(score_date)
    warnings = []

    earnings_warns = _check_earnings(tickers, d, conn)
    warnings.extend(earnings_warns)

    fomc_warn = _check_fomc(d)
    if fomc_warn:
        warnings.append(fomc_warn)

    opex_warn = _check_options_expiry(d)
    if opex_warn:
        warnings.append(opex_warn)

    return warnings


def _check_earnings(tickers: list[str], d: date, conn: sa.engine.Connection) -> list[str]:
    if not tickers:
        return []
    window_end = str(d + timedelta(days=_EARNINGS_WARN_DAYS))
    rows = conn.execute(
        sa.select(
            earnings_calendar.c.ticker,
            earnings_calendar.c.earnings_date,
        ).where(
            earnings_calendar.c.ticker.in_(tickers) &
            (earnings_calendar.c.earnings_date >= str(d)) &
            (earnings_calendar.c.earnings_date <= window_end)
        )
    ).fetchall()

    warns = []
    for ticker, earn_date in rows:
        warns.append(
            f"WARNING: {ticker} earnings on {earn_date} "
            f"(within {_EARNINGS_WARN_DAYS} days of {d})"
        )
    return warns


def _check_fomc(d: date) -> str | None:
    for meeting_str in _FOMC_2026:
        meeting = date.fromisoformat(meeting_str)
        delta = abs((meeting - d).days)
        if delta <= _FOMC_WARN_DAYS:
            return (
                f"WARNING: FOMC meeting on {meeting_str} "
                f"({delta} days from score date)"
            )
    return None


def _check_options_expiry(d: date) -> str | None:
    """Third Friday of d's month — warn if within _OPEX_WARN_DAYS."""
    opex = _third_friday(d.year, d.month)
    delta = abs((opex - d).days)
    if delta <= _OPEX_WARN_DAYS:
        return (
            f"WARNING: Monthly options expiration on {opex} "
            f"({delta} days from score date)"
        )
    return None


def _third_friday(year: int, month: int) -> date:
    """Return the third Friday of the given month."""
    first_of_month = date(year, month, 1)
    # weekday(): Monday=0 … Friday=4
    days_to_friday = (4 - first_of_month.weekday()) % 7
    first_friday   = first_of_month + timedelta(days=days_to_friday)
    return first_friday + timedelta(weeks=2)
