"""Yahoo snapshot fetching: retry with backoff, shared .info and earningsTrend per run, second
pass, coverage."""

import logging
import time
from collections.abc import Callable

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

RETRY_DELAYS = (5, 15)  # waits between the 3 attempts, seconds
SECOND_PASS_PAUSE = 60  # seconds before retrying a step's failed tickers
COVERAGE_MIN = 0.95
BREAKER_FAILURES = 10  # consecutive failed tickers that end a pass early
# yfinance eps_trend column -> stored name; earnings_estimate's 'avg' is kept as 'avg'
TREND_COLUMNS = {
    "current": "current",
    "7daysAgo": "days_7",
    "30daysAgo": "days_30",
    "60daysAgo": "days_60",
    "90daysAgo": "days_90",
}


def call_with_retry[T](fetch: Callable[[], T]) -> T:
    """Return fetch(), retrying after each exception; re-raise the last one."""
    for delay in RETRY_DELAYS:
        try:
            return fetch()
        except Exception:  # any yfinance failure, throttling included, is retried
            time.sleep(delay)
    return fetch()


class YahooInfo:
    """Memoised yfinance .info fetches for one run; failures are not memoised."""

    def __init__(self) -> None:
        self._cache: dict[str, dict] = {}

    def has(self, ticker: str) -> bool:
        return ticker in self._cache

    def get(self, ticker: str) -> dict | None:
        """The ticker's .info dict, or None when every attempt failed."""
        if ticker in self._cache:
            return self._cache[ticker]
        try:
            info = call_with_retry(lambda: yf.Ticker(ticker).info)
        except Exception as exc:
            logger.debug("Yahoo .info fetch failed %s: %s", ticker, exc)
            return None
        self._cache[ticker] = info
        return info


class YahooTrend:
    """Memoised yfinance earningsTrend fetches for one run; failures are not memoised."""

    def __init__(self) -> None:
        self._cache: dict[str, dict] = {}

    def get(self, ticker: str) -> dict | None:
        """{period: {current, days_7, days_30, days_60, days_90, avg}}, {} without coverage,
        or None when every attempt failed. Missing values are None."""
        if ticker in self._cache:
            return self._cache[ticker]
        try:
            eps_trend, estimate = call_with_retry(lambda: _trend_frames(yf.Ticker(ticker)))
        except Exception as exc:
            logger.debug("Yahoo earningsTrend fetch failed %s: %s", ticker, exc)
            return None
        trend = _by_period(eps_trend, TREND_COLUMNS)
        for period, values in _by_period(estimate, {"avg": "avg"}).items():
            trend.setdefault(period, dict.fromkeys(TREND_COLUMNS.values())).update(values)
        self._cache[ticker] = trend
        return trend


def _trend_frames(ticker: yf.Ticker) -> tuple[pd.DataFrame, pd.DataFrame]:
    # Both read the Ticker's one quoteSummary earningsTrend response: a single request.
    return ticker.eps_trend, ticker.earnings_estimate


def _by_period(frame: pd.DataFrame | None, columns: dict[str, str]) -> dict[str, dict]:
    if frame is None or frame.empty:
        return {}
    numeric = frame.reindex(columns=list(columns)).apply(pd.to_numeric, errors="coerce")
    numeric = numeric.astype(object).where(numeric.notna(), None).rename(columns=columns)
    return {str(period): row.to_dict() for period, row in numeric.iterrows()}


def _run_pass(tickers: list[str], fetch_one: Callable[[str], bool], step: str) -> list[str]:
    """Run fetch_one over tickers; return the failures.

    After BREAKER_FAILURES failures in a row Yahoo is refusing everything, so the rest of the
    pass is skipped (counted as failed) instead of waiting out every ticker's retries.
    """
    failed: list[str] = []
    in_a_row = 0
    for i, ticker in enumerate(tickers):
        if in_a_row >= BREAKER_FAILURES:
            skipped = tickers[i:]
            logger.warning(
                "%s: %d consecutive failures; skipping the remaining %d tickers in this pass",
                step,
                BREAKER_FAILURES,
                len(skipped),
            )
            return failed + skipped
        if fetch_one(ticker):
            in_a_row = 0
        else:
            failed.append(ticker)
            in_a_row += 1
    return failed


def fetch_in_two_passes(
    tickers: list[str], fetch_one: Callable[[str], bool], step: str = "Yahoo snapshot"
) -> int:
    """Run fetch_one over tickers, then once more over failures; return the number fetched."""
    failed = _run_pass(tickers, fetch_one, step)
    if failed:
        logger.info("%d tickers failed; second pass in %ds", len(failed), SECOND_PASS_PAUSE)
        time.sleep(SECOND_PASS_PAUSE)
        failed = _run_pass(failed, fetch_one, step)
    return len(tickers) - len(failed)


def log_coverage(step: str, fetched: int, attempted: int, detail: str = "") -> None:
    """INFO the step's coverage; ERROR when it is below COVERAGE_MIN."""
    logger.info("%s: %d/%d tickers fetched%s", step, fetched, attempted, detail)
    log_low_coverage(step, fetched, attempted)


def log_low_coverage(step: str, fetched: int, attempted: int) -> None:
    """ERROR when coverage is below COVERAGE_MIN."""
    if attempted and fetched / attempted < COVERAGE_MIN:
        logger.error(
            "%s: %d/%d tickers fetched after retries (below 95%%)", step, fetched, attempted
        )
