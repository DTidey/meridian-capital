"""Analyst estimates snapshots — forward EPS + price targets via yfinance, plus the
earningsTrend consensus history per fiscal year."""

import logging

import sqlalchemy as sa

from data._utils import utc_now

from .db import analyst_estimates, estimate_trends, insert_or_replace
from .yahoo_info import (
    TREND_COLUMNS,
    YahooInfo,
    YahooTrend,
    fetch_in_two_passes,
    log_coverage,
    log_low_coverage,
)

logger = logging.getLogger(__name__)

TREND_PERIODS = ("0y", "+1y")  # fiscal years stored from earningsTrend


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


def _store_trend(conn: sa.engine.Connection, ticker: str, stamp: tuple[str, str], trend: dict):
    """Upsert the ticker's 0y and +1y rows; a period without a current consensus is skipped."""
    today, now = stamp
    for period in TREND_PERIODS:
        values = trend.get(period) or {}
        if values.get("current") is None:
            continue
        try:
            conn.execute(
                insert_or_replace(conn, estimate_trends).values(
                    ticker=ticker,
                    date=today,
                    period=period,
                    fetched_at=now,
                    **{col: values.get(col) for col in TREND_COLUMNS.values()},
                )
            )
        except Exception as exc:
            logger.debug("Estimate trend insert error %s %s: %s", ticker, period, exc)


def _update_trends(conn, tickers: list[str], trend: YahooTrend, stamp: tuple[str, str]) -> int:
    """Snapshot every ticker's trend (re-runs the same day overwrite); return the number fetched."""

    def fetch_one(ticker: str) -> bool:
        got = trend.get(ticker)
        if got is None:
            return False
        _store_trend(conn, ticker, stamp, got)
        return True

    return fetch_in_two_passes(tickers, fetch_one, "Analyst estimate trends")


def update_estimates(
    conn: sa.engine.Connection,
    tickers: list[str],
    info: YahooInfo | None = None,
    trend: YahooTrend | None = None,
) -> dict[str, bool]:
    """Snapshot today's analyst estimates and estimate trends for all tickers."""
    info = YahooInfo() if info is None else info
    trend = YahooTrend() if trend is None else trend
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

    summary.update(dict.fromkeys(to_fetch, False))  # tickers a tripped breaker skips stay False
    fetched = fetch_in_two_passes(to_fetch, fetch_one, "Analyst estimates")
    trends = _update_trends(conn, tickers, trend, (today, now))
    conn.commit()
    detail = f" ({len(shared)} from the shared .info); trends {trends}/{len(tickers)}"
    log_coverage("Analyst estimates", fetched, len(to_fetch), detail)
    log_low_coverage("Analyst estimate trends", trends, len(tickers))
    updated = sum(1 for v in summary.values() if v)
    logger.info("Estimates complete — %d/%d tickers updated", updated, len(to_fetch))
    return summary
