"""Market cap snapshots via yfinance .info (packet 42)."""

import logging

import sqlalchemy as sa

from data._utils import utc_now

from .db import insert_or_replace, market_caps
from .fundamentals import _safe
from .yahoo_info import YahooInfo, fetch_in_two_passes, log_coverage

logger = logging.getLogger(__name__)


def _parse_market_cap(info: dict) -> float | None:
    """Yahoo's marketCap, or None when it is missing, zero or negative."""
    value = _safe(info.get("marketCap"))
    return value if value is not None and value > 0 else None


def update_market_caps(
    conn: sa.engine.Connection,
    tickers: list[str],
    info: YahooInfo | None = None,
) -> dict[str, bool]:
    """Store today's market cap for tickers that have none yet; {ticker: stored}."""
    info = YahooInfo() if info is None else info
    today = utc_now().strftime("%Y-%m-%d")
    now = utc_now().isoformat(timespec="seconds")
    already = set(
        conn.execute(sa.select(market_caps.c.ticker).where(market_caps.c.date == today)).scalars()
    )
    to_fetch = [t for t in tickers if t not in already]
    logger.info(
        "Market caps: fetching %d tickers (%d already have today's snapshot)",
        len(to_fetch),
        len(already),
    )
    summary: dict[str, bool] = dict.fromkeys(to_fetch, False)

    def fetch_one(ticker: str) -> bool:
        raw = info.get(ticker)
        if raw is None:
            return False
        value = _parse_market_cap(raw)
        if value is not None:
            conn.execute(
                insert_or_replace(conn, market_caps).values(
                    ticker=ticker, date=today, market_cap=value, fetched_at=now
                )
            )
            summary[ticker] = True
        return True

    fetched = fetch_in_two_passes(to_fetch, fetch_one, "Market caps")
    conn.commit()
    stored = sum(summary.values())
    log_coverage("Market caps", fetched, len(to_fetch), f", {stored} stored")
    return summary
