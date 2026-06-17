"""Short interest snapshots via yfinance .info."""

import logging
from datetime import datetime

import sqlalchemy as sa
import yfinance as yf

from .db import insert_or_replace, short_interest

logger = logging.getLogger(__name__)


def _fetch_short_interest(ticker: str) -> dict | None:
    try:
        info = yf.Ticker(ticker).info
        shares_short    = info.get("sharesShort")
        short_ratio     = info.get("shortRatio")
        short_pct_float = info.get("shortPercentOfFloat")
        if shares_short is None and short_ratio is None and short_pct_float is None:
            return None
        return {
            "shares_short": float(shares_short) if shares_short is not None else None,
            "short_ratio": float(short_ratio) if short_ratio is not None else None,
            "short_pct_float": float(short_pct_float) if short_pct_float is not None else None,
        }
    except Exception as exc:
        logger.debug("Short interest fetch failed %s: %s", ticker, exc)
        return None


def update_short_interest(
    conn: sa.engine.Connection,
    tickers: list[str],
) -> dict[str, bool]:
    """Fetch today's short interest snapshot for all tickers."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    now   = datetime.utcnow().isoformat(timespec="seconds")
    summary: dict[str, bool] = {}

    already = set(conn.execute(
        sa.select(short_interest.c.ticker)
        .where(short_interest.c.date == today)
    ).scalars().all())

    to_fetch = [t for t in tickers if t not in already]
    logger.info("Short interest: fetching %d tickers (%d already have today's snapshot)",
                len(to_fetch), len(already))

    for ticker in to_fetch:
        data = _fetch_short_interest(ticker)
        if data:
            try:
                conn.execute(
                    insert_or_replace(conn, short_interest).values(
                        ticker=ticker, date=today,
                        shares_short=data["shares_short"],
                        short_ratio=data["short_ratio"],
                        short_pct_float=data["short_pct_float"],
                        fetched_at=now,
                    )
                )
                summary[ticker] = True
            except Exception as exc:
                logger.debug("Short interest insert error %s: %s", ticker, exc)
                summary[ticker] = False
        else:
            summary[ticker] = False

    conn.commit()
    updated = sum(1 for v in summary.values() if v)
    logger.info("Short interest complete — %d/%d tickers updated", updated, len(to_fetch))
    return summary
