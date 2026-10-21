"""Yahoo snapshot fetching: retry with backoff, shared .info per run, second pass, coverage."""

import logging
import time
from collections.abc import Callable

import yfinance as yf

logger = logging.getLogger(__name__)

RETRY_DELAYS = (5, 15)  # waits between the 3 attempts, seconds
SECOND_PASS_PAUSE = 60  # seconds before retrying a step's failed tickers
COVERAGE_MIN = 0.95


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


def fetch_in_two_passes(tickers: list[str], fetch_one: Callable[[str], bool]) -> int:
    """Run fetch_one over tickers, then once more over failures; return the number fetched."""
    failed = [t for t in tickers if not fetch_one(t)]
    if failed:
        logger.info("%d tickers failed; second pass in %ds", len(failed), SECOND_PASS_PAUSE)
        time.sleep(SECOND_PASS_PAUSE)
        failed = [t for t in failed if not fetch_one(t)]
    return len(tickers) - len(failed)


def log_coverage(step: str, fetched: int, attempted: int, detail: str = "") -> None:
    """INFO the step's coverage; ERROR when it is below COVERAGE_MIN."""
    logger.info("%s: %d/%d tickers fetched%s", step, fetched, attempted, detail)
    if attempted and fetched / attempted < COVERAGE_MIN:
        logger.error(
            "%s: %d/%d tickers fetched after retries (below 95%%)", step, fetched, attempted
        )
