"""Daily OHLCV price ingestion — incremental updates via yfinance or Polygon."""

import logging
from datetime import datetime, timedelta

import pandas as pd
import sqlalchemy as sa
import yfinance as yf

from .db import daily_prices, insert_or_replace
from .providers import PriceProvider, Providers

logger = logging.getLogger(__name__)

_BATCH = 100   # tickers per yfinance download call


def _last_stored_date(conn: sa.engine.Connection, ticker: str) -> str | None:
    return conn.execute(
        sa.select(sa.func.max(daily_prices.c.date))
        .where(daily_prices.c.ticker == ticker)
    ).scalar()


def _upsert_prices(conn: sa.engine.Connection, df: pd.DataFrame, ticker: str) -> int:
    if df.empty:
        return 0
    rows = []
    for idx, row in df.iterrows():
        date_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
        rows.append({
            "ticker":    ticker,
            "date":      date_str,
            "open":      _float(row.get("Open")),
            "high":      _float(row.get("High")),
            "low":       _float(row.get("Low")),
            "close":     _float(row.get("Close")),
            "adj_close": _float(row.get("Adj Close") if pd.notna(row.get("Adj Close")) else row.get("Close")),
            "volume":    int(_float(row.get("Volume")) or 0),
        })
    conn.execute(insert_or_replace(conn, daily_prices), rows)
    conn.commit()
    return len(rows)


def _float(val) -> float | None:
    try:
        f = float(val)
        return None if (f != f) else f   # NaN check
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
        rows.append({
            "Open": bar.get("o"),
            "High": bar.get("h"),
            "Low": bar.get("l"),
            "Close": bar.get("c"),
            "Adj Close": bar.get("c"),
            "Volume": bar.get("v"),
        })
        dates = pd.to_datetime([b["t"] for b in data["results"]], unit="ms", utc=True)
    df = pd.DataFrame(rows, index=dates.tz_convert(None))
    return df


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def update_prices(
    conn: sa.engine.Connection,
    tickers: list[str],
    config: dict,
    providers: Providers,
) -> dict[str, int]:
    """Incrementally update daily prices. Returns {ticker: bars_added}."""
    lookback_years = config["market_data"]["lookback_years"]
    cutoff = (datetime.utcnow() - timedelta(days=365 * lookback_years)).strftime("%Y-%m-%d")
    today = datetime.utcnow().strftime("%Y-%m-%d")

    summary: dict[str, int] = {}

    if providers.prices == PriceProvider.POLYGON:
        logger.info("Fetching prices via Polygon for %d tickers", len(tickers))
        for ticker in tickers:
            last = _last_stored_date(conn, ticker)
            start = max(last, cutoff) if last else cutoff
            if start >= today:
                summary[ticker] = 0
                continue
            df = _fetch_polygon(ticker, start, today, providers.polygon_key)
            added = _upsert_prices(conn, df, ticker)
            summary[ticker] = added
            if added:
                logger.debug("Polygon %s: +%d bars", ticker, added)
        return summary

    logger.info("Fetching prices via yfinance for %d tickers", len(tickers))

    by_start: dict[str, list[str]] = {}
    for ticker in tickers:
        last = _last_stored_date(conn, ticker)
        start = max(last, cutoff) if last else cutoff
        if start >= today:
            summary[ticker] = 0
            continue
        by_start.setdefault(start, []).append(ticker)

    for start, batch_tickers in by_start.items():
        for i in range(0, len(batch_tickers), _BATCH):
            chunk = batch_tickers[i: i + _BATCH]
            batch_data = _fetch_yfinance_batch(chunk, start, today)

            for ticker in chunk:
                df = batch_data.get(ticker)
                if df is None or df.empty:
                    df = _fetch_yfinance_single(ticker, start, today)
                added = _upsert_prices(conn, df, ticker) if df is not None else 0
                summary[ticker] = added
                if added:
                    logger.debug("yfinance %s: +%d bars", ticker, added)

    total_added = sum(summary.values())
    logger.info("Prices update complete — %d new bars across %d tickers",
                total_added, len([v for v in summary.values() if v > 0]))
    return summary
