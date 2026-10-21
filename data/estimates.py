"""Analyst estimates snapshots — forward EPS + price targets via yfinance."""

import logging

import sqlalchemy as sa

from data._utils import utc_now

from .db import analyst_estimates, insert_or_replace
from .yahoo_info import YahooInfo, fetch_in_two_passes, log_coverage

logger = logging.getLogger(__name__)


def _parse_estimates(ticker: str, info: dict) -> dict | None:
    try:
        eps_fwd = info.get("forwardEps")
        price_target = info.get("targetMeanPrice") or info.get("targetMedianPrice")
        num_analysts = info.get("numberOfAnalystOpinions") or info.get("recommendationMean")

        if eps_fwd is None and price_target is None:
            return None

        return {
            "eps_estimate_fwd": float(eps_fwd) if eps_fwd is not None else None,
            "price_target": float(price_target) if price_target is not None else None,
            "num_analysts": int(num_analysts)
            if num_analysts is not None and isinstance(num_analysts, (int, float))
            else None,
        }
    except Exception as exc:
        logger.debug("Estimates parse failed %s: %s", ticker, exc)
        return None


def _store(conn: sa.engine.Connection, ticker: str, today: str, now: str, data: dict) -> bool:
    try:
        conn.execute(
            insert_or_replace(conn, analyst_estimates).values(
                ticker=ticker,
                date=today,
                eps_estimate_fwd=data["eps_estimate_fwd"],
                price_target=data["price_target"],
                num_analysts=data["num_analysts"],
                fetched_at=now,
            )
        )
        return True
    except Exception as exc:
        logger.debug("Estimates insert error %s: %s", ticker, exc)
        return False


def update_estimates(
    conn: sa.engine.Connection,
    tickers: list[str],
    info: YahooInfo | None = None,
) -> dict[str, bool]:
    """Snapshot today's analyst estimates for all tickers."""
    info = YahooInfo() if info is None else info
    today = utc_now().strftime("%Y-%m-%d")
    now = utc_now().isoformat(timespec="seconds")
    summary: dict[str, bool] = {}
    shared: set[str] = set()

    already = set(
        conn.execute(sa.select(analyst_estimates.c.ticker).where(analyst_estimates.c.date == today))
        .scalars()
        .all()
    )

    to_fetch = [t for t in tickers if t not in already]
    logger.info(
        "Analyst estimates: fetching %d tickers (%d already updated today)",
        len(to_fetch),
        len(already),
    )

    def fetch_one(ticker: str) -> bool:
        if info.has(ticker):
            shared.add(ticker)
        raw = info.get(ticker)
        if raw is None:
            summary[ticker] = False
            return False
        data = _parse_estimates(ticker, raw)
        summary[ticker] = bool(data) and _store(conn, ticker, today, now, data)
        return True

    fetched = fetch_in_two_passes(to_fetch, fetch_one)
    conn.commit()
    log_coverage(
        "Analyst estimates", fetched, len(to_fetch), f" ({len(shared)} from the shared .info)"
    )
    updated = sum(1 for v in summary.values() if v)
    logger.info("Estimates complete — %d/%d tickers updated", updated, len(to_fetch))
    return summary
