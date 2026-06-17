"""Analyst estimates snapshots — forward EPS + price targets via yfinance."""

import logging
from datetime import datetime

import sqlalchemy as sa
import yfinance as yf

from .db import analyst_estimates, insert_or_replace

logger = logging.getLogger(__name__)


def _fetch_estimates(ticker: str) -> dict | None:
    try:
        info = yf.Ticker(ticker).info
        eps_fwd      = info.get("forwardEps")
        price_target = info.get("targetMeanPrice") or info.get("targetMedianPrice")
        num_analysts = info.get("numberOfAnalystOpinions") or info.get("recommendationMean")

        if eps_fwd is None and price_target is None:
            return None

        return {
            "eps_estimate_fwd": float(eps_fwd) if eps_fwd is not None else None,
            "price_target": float(price_target) if price_target is not None else None,
            "num_analysts": int(num_analysts) if num_analysts is not None and isinstance(num_analysts, (int, float)) else None,
        }
    except Exception as exc:
        logger.debug("Estimates fetch failed %s: %s", ticker, exc)
        return None


def update_estimates(
    conn: sa.engine.Connection,
    tickers: list[str],
) -> dict[str, bool]:
    """Snapshot today's analyst estimates for all tickers."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    now   = datetime.utcnow().isoformat(timespec="seconds")
    summary: dict[str, bool] = {}

    already = set(conn.execute(
        sa.select(analyst_estimates.c.ticker)
        .where(analyst_estimates.c.date == today)
    ).scalars().all())

    to_fetch = [t for t in tickers if t not in already]
    logger.info("Analyst estimates: fetching %d tickers (%d already updated today)",
                len(to_fetch), len(already))

    for ticker in to_fetch:
        data = _fetch_estimates(ticker)
        if data:
            try:
                conn.execute(
                    insert_or_replace(conn, analyst_estimates).values(
                        ticker=ticker, date=today,
                        eps_estimate_fwd=data["eps_estimate_fwd"],
                        price_target=data["price_target"],
                        num_analysts=data["num_analysts"],
                        fetched_at=now,
                    )
                )
                summary[ticker] = True
            except Exception as exc:
                logger.debug("Estimates insert error %s: %s", ticker, exc)
                summary[ticker] = False
        else:
            summary[ticker] = False

    conn.commit()
    updated = sum(1 for v in summary.values() if v)
    logger.info("Estimates complete — %d/%d tickers updated", updated, len(to_fetch))
    return summary
