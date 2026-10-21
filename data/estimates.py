"""Analyst estimates snapshots — forward EPS + price targets via yfinance, plus the
earningsTrend consensus history per fiscal year."""

import logging

import sqlalchemy as sa

from data._utils import utc_now

from .db import analyst_estimates, estimate_trends, insert_or_replace
from .yahoo_info import (
    COVERAGE_MIN,
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


def _store_trend(
    conn: sa.engine.Connection, ticker: str, stamp: tuple[str, str], trend: dict
) -> bool:
    """Upsert the ticker's 0y and +1y rows (a period without a current consensus is skipped);
    return True when a +1y row was stored."""
    today, now = stamp
    stored_plus_1y = False
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
            stored_plus_1y = stored_plus_1y or period == "+1y"
        except Exception as exc:
            logger.debug("Estimate trend insert error %s %s: %s", ticker, period, exc)
    return stored_plus_1y


def _update_trends(
    conn, tickers: list[str], trend: YahooTrend, stamp: tuple[str, str]
) -> tuple[int, int]:
    """Snapshot every ticker's trend (re-runs the same day overwrite).

    Returns (tickers fetched, tickers with a +1y row stored).
    """
    with_plus_1y: set[str] = set()

    def fetch_one(ticker: str) -> bool:
        got = trend.get(ticker)
        if got is None:
            return False
        if _store_trend(conn, ticker, stamp, got):
            with_plus_1y.add(ticker)
        return True

    fetched = fetch_in_two_passes(tickers, fetch_one, "Analyst estimate trends")
    return fetched, len(with_plus_1y)


def _log_low_plus_1y(stored: int, fetched: int) -> None:
    """ERROR when too few tickers got a +1y consensus: yfinance hides some HTTP errors as an
    empty earningsTrend, which the fetch coverage cannot see."""
    if fetched and stored / fetched < COVERAGE_MIN:
        logger.error(
            "Analyst estimate trends: %d/%d tickers have a +1y consensus (below 95%%)",
            stored,
            fetched,
        )


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
    trends, plus_1y = _update_trends(conn, tickers, trend, (today, now))
    conn.commit()
    detail = f" ({len(shared)} from the shared .info); trends {trends}/{len(tickers)}"
    log_coverage("Analyst estimates", fetched, len(to_fetch), detail)
    log_low_coverage("Analyst estimate trends", trends, len(tickers))
    _log_low_plus_1y(plus_1y, trends)
    updated = sum(1 for v in summary.values() if v)
    logger.info("Estimates complete — %d/%d tickers updated", updated, len(to_fetch))
    return summary
