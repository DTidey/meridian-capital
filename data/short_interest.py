"""Short interest snapshots via yfinance .info."""

import logging

import sqlalchemy as sa

from data._utils import utc_now

from .db import insert_or_replace, short_interest
from .yahoo_info import YahooInfo, fetch_in_two_passes, log_coverage

logger = logging.getLogger(__name__)


def _parse_short_interest(ticker: str, info: dict) -> dict | None:
    try:
        shares_short = info.get("sharesShort")
        short_ratio = info.get("shortRatio")
        short_pct_float = info.get("shortPercentOfFloat")
        if shares_short is None and short_ratio is None and short_pct_float is None:
            return None
        return {
            "shares_short": float(shares_short) if shares_short is not None else None,
            "short_ratio": float(short_ratio) if short_ratio is not None else None,
            "short_pct_float": float(short_pct_float) if short_pct_float is not None else None,
        }
    except Exception as exc:
        logger.debug("Short interest parse failed %s: %s", ticker, exc)
        return None


def _store(conn: sa.engine.Connection, ticker: str, today: str, now: str, data: dict) -> bool:
    try:
        conn.execute(
            insert_or_replace(conn, short_interest).values(
                ticker=ticker,
                date=today,
                shares_short=data["shares_short"],
                short_ratio=data["short_ratio"],
                short_pct_float=data["short_pct_float"],
                fetched_at=now,
            )
        )
        return True
    except Exception as exc:
        logger.debug("Short interest insert error %s: %s", ticker, exc)
        return False


def update_short_interest(
    conn: sa.engine.Connection,
    tickers: list[str],
    info: YahooInfo | None = None,
) -> dict[str, bool]:
    """Fetch today's short interest snapshot for all tickers."""
    info = YahooInfo() if info is None else info
    today = utc_now().strftime("%Y-%m-%d")
    now = utc_now().isoformat(timespec="seconds")
    summary: dict[str, bool] = {}

    already = set(
        conn.execute(sa.select(short_interest.c.ticker).where(short_interest.c.date == today))
        .scalars()
        .all()
    )

    to_fetch = [t for t in tickers if t not in already]
    logger.info(
        "Short interest: fetching %d tickers (%d already have today's snapshot)",
        len(to_fetch),
        len(already),
    )

    def fetch_one(ticker: str) -> bool:
        raw = info.get(ticker)
        if raw is None:
            summary[ticker] = False
            return False
        data = _parse_short_interest(ticker, raw)
        summary[ticker] = bool(data) and _store(conn, ticker, today, now, data)
        return True

    summary.update(dict.fromkeys(to_fetch, False))  # tickers a tripped breaker skips stay False
    fetched = fetch_in_two_passes(to_fetch, fetch_one, "Short interest")
    conn.commit()
    log_coverage("Short interest", fetched, len(to_fetch))
    updated = sum(1 for v in summary.values() if v)
    logger.info("Short interest complete — %d/%d tickers updated", updated, len(to_fetch))
    return summary
