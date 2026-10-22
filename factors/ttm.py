"""Trailing-twelve-month sums over quarterly fundamentals (packets 21, 24, 42)."""

from __future__ import annotations

import math
from datetime import date

MAX_SKIP_DAYS = 100  # one quarter
MAX_TTM_SPAN_DAYS = 300  # four contiguous quarter ends are ~9 months apart; a gap makes ~12
# Yahoo omits these cash-flow lines when they are zero
_ZERO_WHEN_OMITTED = ("dividends_paid", "buybacks")


def num(value) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return float(value)


def _quarter_value(quarter: dict, field: str) -> float | None:
    value = num(quarter.get(field))
    if value is None and field in _ZERO_WHEN_OMITTED and num(quarter.get("cfo")) is not None:
        return 0.0  # the cash-flow statement is there, so the line is zero
    return value


def ttm(quarters: list[dict], field: str) -> float | None:
    """Sum over the four latest quarters reporting this field, if they form a trailing year.

    Quarters newest first. Quarters without the field are skipped (Yahoo can list a period
    before publishing its figures); the span check still rejects a gap in the middle of the
    series.
    """
    reported = [(q, v) for q in quarters if (v := _quarter_value(q, field)) is not None][:4]
    if not quarters or len(reported) < 4:
        return None
    newest, first, last = (
        date.fromisoformat(str(q["period_end"])[:10])
        for q in (quarters[0], reported[3][0], reported[0][0])
    )
    # Contiguous, and recent: at most one quarter skipped, so a stopped dividend doesn't
    # show last year's yield
    if (last - first).days > MAX_TTM_SPAN_DAYS or (newest - last).days > MAX_SKIP_DAYS:
        return None
    values = [v for _, v in reported]
    # Cash returned is an outflow; sum the sizes so mixed signs can't cancel
    return sum(abs(v) for v in values) if field in _ZERO_WHEN_OMITTED else sum(values)
