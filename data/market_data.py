"""Daily OHLCV price ingestion — incremental updates via yfinance or Polygon."""

import logging
from bisect import bisect_left, bisect_right
from collections import Counter
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import sqlalchemy as sa
import yfinance as yf

from data._utils import utc_now

from .db import daily_prices, insert_or_replace
from .providers import PriceProvider, Providers

logger = logging.getLogger(__name__)

_BATCH = 100  # tickers per yfinance download call
_NEW_YORK = ZoneInfo("America/New_York")
_SESSION_CLOSED = time(16, 30)  # US daily bar complete from this New York time
_ADJ_TOL = 1e-4  # relative close/adj_close change that means the history was re-adjusted
_REASONS = ("adjustment", "gap", "refresh")
_OHLC = {"open": "Open", "high": "High", "low": "Low", "close": "Close"}


def _last_stored_date(conn: sa.engine.Connection, ticker: str) -> str | None:
    return conn.execute(
        sa.select(sa.func.max(daily_prices.c.date)).where(daily_prices.c.ticker == ticker)
    ).scalar()


def _price_rows(df: pd.DataFrame, ticker: str) -> tuple[list[dict], int]:
    """Rows to store, and the number dropped for having no open, high, low or close."""
    rows, dropped = [], 0
    for idx, row in df.iterrows():
        date_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
        prices = {k: _float(row.get(c)) for k, c in _OHLC.items()}
        if all(v is None for v in prices.values()):
            dropped += 1
            continue
        rows.append(
            {
                "ticker": ticker,
                "date": date_str,
                **prices,
                "adj_close": _float(
                    row.get("Adj Close") if pd.notna(row.get("Adj Close")) else row.get("Close")
                ),
                "volume": int(_float(row.get("Volume")) or 0),
            }
        )
    return rows, dropped


def _upsert_prices(
    conn: sa.engine.Connection, df: pd.DataFrame, ticker: str, stats: Counter | None = None
) -> int:
    if df is None or df.empty:
        return 0
    rows, dropped = _price_rows(df, ticker)
    if stats is not None:
        stats["dropped"] += dropped
    if not rows:
        return 0
    conn.execute(insert_or_replace(conn, daily_prices), rows)
    conn.commit()
    return len(rows)


def _replace_prices(
    conn: sa.engine.Connection, df: pd.DataFrame | None, ticker: str, stats: Counter
) -> int:
    """Replace all of the ticker's rows with `df` in one transaction; keep them if `df` is empty."""
    rows, dropped = _price_rows(df, ticker) if df is not None else ([], 0)
    stats["dropped"] += dropped
    if not rows:
        logger.warning("%s: full re-download returned no usable rows; existing rows kept", ticker)
        return 0
    try:
        conn.execute(sa.delete(daily_prices).where(daily_prices.c.ticker == ticker))
        conn.execute(insert_or_replace(conn, daily_prices), rows)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return len(rows)


def _float(val) -> float | None:
    try:
        f = float(val)
        return None if (f != f) else f  # NaN check
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# yfinance backend
# ---------------------------------------------------------------------------


def _fetch_yfinance_batch(tickers: list[str], start: str, end: str) -> dict[str, pd.DataFrame]:
    joined = " ".join(tickers)
    try:
        raw = yf.download(
            joined,
            start=start,
            end=end,
            auto_adjust=False,
            progress=False,
            threads=True,
        )
    except Exception as exc:
        logger.error("yfinance download failed for batch: %s", exc)
        return {}

    if raw.empty:
        return {}

    result: dict[str, pd.DataFrame] = {}
    if isinstance(raw.columns, pd.MultiIndex):
        for ticker in tickers:
            try:
                sub = raw.xs(ticker, axis=1, level=1)
                if not sub.empty:
                    result[ticker] = sub
            except KeyError:
                pass
    else:
        if len(tickers) == 1:
            result[tickers[0]] = raw
    return result


def _fetch_yfinance_single(ticker: str, start: str, end: str) -> pd.DataFrame:
    try:
        t = yf.Ticker(ticker)
        df = t.history(start=start, end=end, auto_adjust=False)
        return df
    except Exception as exc:
        logger.warning("yfinance single fetch failed for %s: %s", ticker, exc)
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Polygon backend
# ---------------------------------------------------------------------------


def _fetch_polygon(ticker: str, start: str, end: str, api_key: str) -> pd.DataFrame:
    """Fetch daily OHLCV from Polygon REST API."""
    import requests

    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}"
        f"?adjusted=true&sort=asc&limit=5000&apiKey={api_key}"
    )
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Polygon fetch failed for %s: %s", ticker, exc)
        return pd.DataFrame()

    if data.get("status") not in ("OK", "DELAYED") or not data.get("results"):
        return pd.DataFrame()

    rows = []
    for bar in data["results"]:
        rows.append(
            {
                "Open": bar.get("o"),
                "High": bar.get("h"),
                "Low": bar.get("l"),
                "Close": bar.get("c"),
                "Adj Close": bar.get("c"),
                "Volume": bar.get("v"),
            }
        )
        dates = pd.to_datetime([b["t"] for b in data["results"]], unit="ms", utc=True)
    df = pd.DataFrame(rows, index=dates.tz_convert(None))
    return df


# ---------------------------------------------------------------------------
# Download window and integrity checks
# ---------------------------------------------------------------------------


def _ny_now() -> datetime:
    return datetime.now(_NEW_YORK)


def _download_end(now: datetime | None = None) -> str:
    """Exclusive download end: tomorrow once today's US session has closed, else today."""
    ny = (now or _ny_now()).astimezone(_NEW_YORK)
    end = ny.date() + timedelta(days=1) if ny.time() >= _SESSION_CLOSED else ny.date()
    return end.isoformat()


def _calendar_dates(conn: sa.engine.Connection) -> list[str]:
    """Dates on which at least half of all stored tickers have a close."""
    n_tickers = conn.execute(sa.select(sa.func.count(sa.distinct(daily_prices.c.ticker)))).scalar()
    query = (
        sa.select(daily_prices.c.date)
        .where(daily_prices.c.close.is_not(None))
        .group_by(daily_prices.c.date)
        .having(sa.func.count() * 2 >= n_tickers)
        .order_by(daily_prices.c.date)
    )
    return list(conn.execute(query).scalars())


def _gap_tickers(conn: sa.engine.Connection, tickers: list[str]) -> list[str]:
    """Tickers with fewer closes than calendar dates between their first and last close."""
    calendar = _calendar_dates(conn)
    spans = conn.execute(
        sa.select(
            daily_prices.c.ticker,
            sa.func.min(daily_prices.c.date),
            sa.func.max(daily_prices.c.date),
            sa.func.count(),
        )
        .where(daily_prices.c.close.is_not(None))
        .group_by(daily_prices.c.ticker)
    ).all()
    short = {
        ticker
        for ticker, first, last, n_rows in spans
        if n_rows < bisect_right(calendar, last) - bisect_left(calendar, first)
    }
    return [t for t in tickers if t in short]


def _adjustment_changed(
    conn: sa.engine.Connection, df: pd.DataFrame, ticker: str, last: str
) -> bool:
    """True when the provider's close or adj_close for `last` differs from the stored row."""
    stored = conn.execute(
        sa.select(daily_prices.c.close, daily_prices.c.adj_close).where(
            (daily_prices.c.ticker == ticker) & (daily_prices.c.date == last)
        )
    ).first()
    fetched = next((r for r in _price_rows(df, ticker)[0] if r["date"] == last), None)
    if stored is None or fetched is None:
        return False
    for old, new in zip(stored, (fetched["close"], fetched["adj_close"]), strict=True):
        if old is not None and new is not None and abs(new - old) > _ADJ_TOL * abs(old):
            return True
    return False


# ---------------------------------------------------------------------------
# Fetching and storing
# ---------------------------------------------------------------------------


def _fetch(
    tickers: list[str], start: str, end: str, providers: Providers
) -> dict[str, pd.DataFrame]:
    """Daily bars for `tickers` from `start` to `end` (exclusive) from the configured backend."""
    if providers.prices == PriceProvider.POLYGON:
        return {t: _fetch_polygon(t, start, end, providers.polygon_key) for t in tickers}
    result: dict[str, pd.DataFrame] = {}
    for i in range(0, len(tickers), _BATCH):
        chunk = tickers[i : i + _BATCH]
        batch_data = _fetch_yfinance_batch(chunk, start, end)
        for ticker in chunk:
            df = batch_data.get(ticker)
            if df is None or df.empty:
                df = _fetch_yfinance_single(ticker, start, end)
            result[ticker] = df
    return result


def _update_incremental(
    conn: sa.engine.Connection,
    tickers: list[str],
    window: tuple[str, str],
    providers: Providers,
    redownload: dict[str, str],
    stats: Counter,
) -> dict[str, int]:
    """Fetch from each ticker's last stored date; flag re-adjusted histories in `redownload`."""
    cutoff, end = window
    summary: dict[str, int] = {}
    by_start: dict[str, list[str]] = {}
    last_dates: dict[str, str | None] = {}
    for ticker in tickers:
        last_dates[ticker] = _last_stored_date(conn, ticker)
        start = max(last_dates[ticker], cutoff) if last_dates[ticker] else cutoff
        if start >= end:
            summary[ticker] = 0
            continue
        by_start.setdefault(start, []).append(ticker)

    for start, group in by_start.items():
        for ticker, df in _fetch(group, start, end, providers).items():
            last = last_dates[ticker]
            if last and df is not None and _adjustment_changed(conn, df, ticker, last):
                redownload[ticker] = "adjustment"
                continue
            summary[ticker] = _upsert_prices(conn, df, ticker, stats)
            if summary[ticker]:
                logger.debug("%s: +%d bars", ticker, summary[ticker])
    return summary


def _redownload_full(
    conn: sa.engine.Connection,
    redownload: dict[str, str],
    window: tuple[str, str],
    providers: Providers,
    stats: Counter,
) -> dict[str, int]:
    """Replace each ticker's stored history with a fresh download from the cutoff."""
    cutoff, end = window
    for ticker, reason in redownload.items():
        logger.info("%s: full re-download (%s)", ticker, reason)
    fetched = _fetch(list(redownload), cutoff, end, providers)
    return {t: _replace_prices(conn, fetched.get(t), t, stats) for t in redownload}


def _log_counts(redownload: dict[str, str], stats: Counter) -> None:
    by_reason = Counter(redownload.values())
    parts = ", ".join(f"{by_reason[r]} {r}" for r in _REASONS if by_reason[r])
    logger.info(
        "Prices: %d full re-downloads%s, %d empty rows dropped",
        len(redownload),
        f" ({parts})" if parts else "",
        stats["dropped"],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def update_prices(
    conn: sa.engine.Connection,
    tickers: list[str],
    config: dict,
    providers: Providers,
    refresh: bool = False,
) -> dict[str, int]:
    """Update daily prices; re-download a ticker's full history when it no longer matches the
    provider, or for every ticker when `refresh`. Returns {ticker: bars_stored}."""
    lookback_years = config["market_data"]["lookback_years"]
    cutoff = (utc_now() - timedelta(days=365 * lookback_years)).strftime("%Y-%m-%d")
    window = (cutoff, _download_end())
    logger.info(
        "Fetching prices via %s for %d tickers (end %s, exclusive)",
        providers.prices.value,
        len(tickers),
        window[1],
    )

    stats: Counter = Counter()
    reason = "refresh" if refresh else "gap"
    redownload = dict.fromkeys(tickers if refresh else _gap_tickers(conn, tickers), reason)
    incremental = [t for t in tickers if t not in redownload]
    summary = _update_incremental(conn, incremental, window, providers, redownload, stats)
    if redownload:
        summary.update(_redownload_full(conn, redownload, window, providers, stats))
    _log_counts(redownload, stats)

    logger.info(
        "Prices update complete — %d bars stored across %d tickers",
        sum(summary.values()),
        len([v for v in summary.values() if v > 0]),
    )
    return summary
