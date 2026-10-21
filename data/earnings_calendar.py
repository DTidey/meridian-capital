"""Earnings calendar — upcoming dates for the next N days from Yahoo .info, with the quarterly
EPS estimate from earningsTrend."""

import logging
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from data._utils import utc_now

from .db import earnings_calendar, insert_or_replace
from .yahoo_info import YahooInfo, YahooTrend, fetch_in_two_passes, log_coverage

logger = logging.getLogger(__name__)


def _fetch_earnings_date(ticker: str, info: YahooInfo, trend: YahooTrend) -> list[dict] | None:
    """Upcoming earnings dates for one ticker; None when .info failed, [] when it had none."""
    raw = info.get(ticker)
    if raw is None:
        return None
    dates = _info_dates(raw)
    if not dates:
        return []
    quarter = (trend.get(ticker) or {}).get("0q") or {}
    return [{"earnings_date": d, "eps_estimate": quarter.get("avg")} for d in dates]


def _info_dates(raw: dict) -> list[str]:
    """earningsTimestampStart, plus earningsTimestampEnd when it is a different UTC date."""
    start = _utc_date(raw.get("earningsTimestampStart"))
    if start is None:
        return []
    end = _utc_date(raw.get("earningsTimestampEnd"))
    return [start] if end in (None, start) else [start, end]


def _utc_date(timestamp) -> str | None:
    try:
        return datetime.fromtimestamp(int(timestamp), tz=UTC).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _store_entries(conn, ticker: str, entries: list[dict], window: tuple[str, str], now: str):
    """Insert the in-window entries; return (rows stored, in-window dates)."""
    today, cutoff = window
    stored = 0
    dates: set[str] = set()
    for entry in entries:
        ed = entry["earnings_date"]
        if ed < today or ed > cutoff:
            continue
        dates.add(ed)
        try:
            conn.execute(
                insert_or_replace(conn, earnings_calendar).values(
                    ticker=ticker,
                    earnings_date=ed,
                    eps_estimate=entry["eps_estimate"],
                    fetched_at=now,
                )
            )
            stored += 1
        except Exception as exc:
            logger.debug("Earnings calendar insert error %s: %s", ticker, exc)
    return stored, dates


def _delete_superseded(conn, ticker: str, keep: set[str], today: str) -> int:
    """Delete the ticker's future dates not in keep; past dates are never touched."""
    result = conn.execute(
        sa.delete(earnings_calendar).where(
            (earnings_calendar.c.ticker == ticker)
            & (earnings_calendar.c.earnings_date >= today)
            & earnings_calendar.c.earnings_date.not_in(sorted(keep))
        )
    )
    return result.rowcount or 0


def _fetched_today(conn, today: str) -> set[str]:
    query = sa.select(earnings_calendar.c.ticker).distinct()
    return set(conn.execute(query.where(earnings_calendar.c.fetched_at >= today)).scalars())


def update_earnings_calendar(
    conn: sa.engine.Connection,
    tickers: list[str],
    config: dict,
    info: YahooInfo | None = None,
    trend: YahooTrend | None = None,
) -> dict[str, int]:
    """Refresh upcoming earnings dates for all universe tickers."""
    info = YahooInfo() if info is None else info
    trend = YahooTrend() if trend is None else trend
    lookahead = config["earnings_calendar"]["lookahead_days"]
    today = utc_now().strftime("%Y-%m-%d")
    cutoff = (utc_now() + timedelta(days=lookahead)).strftime("%Y-%m-%d")
    now = utc_now().isoformat(timespec="seconds")

    already = _fetched_today(conn, today)
    to_fetch = [t for t in tickers if t not in already]
    logger.info(
        "Earnings calendar: fetching %d tickers (%d already updated today)",
        len(to_fetch),
        len(already),
    )

    summary: dict[str, int] = {t: 0 for t in tickers if t in already}
    removed = 0

    def fetch_one(ticker: str) -> bool:
        nonlocal removed
        entries = _fetch_earnings_date(ticker, info, trend)
        if entries is None:
            summary[ticker] = 0
            return False
        stored, dates = _store_entries(conn, ticker, entries, (today, cutoff), now)
        if stored:
            removed += _delete_superseded(conn, ticker, dates, today)
        summary[ticker] = stored
        return True

    summary.update(dict.fromkeys(to_fetch, 0))  # tickers a tripped breaker skips stay 0
    fetched = fetch_in_two_passes(to_fetch, fetch_one, "Earnings calendar")
    conn.commit()
    total = sum(summary.values())
    log_coverage(
        "Earnings calendar",
        fetched,
        len(to_fetch),
        f", {total} upcoming events stored, {removed} superseded removed",
    )
    return summary
