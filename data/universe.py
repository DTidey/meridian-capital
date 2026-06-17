"""Universe management: S&P 500 from Wikipedia + benchmark tickers."""

import logging
from datetime import datetime, timedelta

import pandas as pd
import requests
import sqlalchemy as sa

from .db import benchmark_tickers, insert_or_replace, sp500_universe

logger = logging.getLogger(__name__)

_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def _cache_is_fresh(conn: sa.engine.Connection, refresh_days: int) -> bool:
    row = conn.execute(
        sa.select(sa.func.max(sp500_universe.c.updated_at))
    ).scalar()
    if not row:
        return False
    last = datetime.fromisoformat(row)
    return datetime.utcnow() - last < timedelta(days=refresh_days)


def fetch_sp500(conn: sa.engine.Connection, config: dict, force: bool = False) -> list[str]:
    """Return list of S&P 500 tickers, refreshing from Wikipedia if stale."""
    refresh_days = config["universe"]["cache_refresh_days"]

    if not force and _cache_is_fresh(conn, refresh_days):
        tickers = conn.execute(sa.select(sp500_universe.c.ticker)).scalars().all()
        logger.info("Universe cache fresh — %d tickers loaded from DB", len(tickers))
        return list(tickers)

    logger.info("Fetching S&P 500 list from Wikipedia")
    try:
        import io
        resp = requests.get(
            _WIKI_URL,
            headers={"User-Agent": "Mozilla/5.0 (compatible; MeridianCapital/1.0)"},
            timeout=30,
        )
        resp.raise_for_status()
        tables = pd.read_html(io.StringIO(resp.text), header=0)
    except Exception as exc:
        logger.error("Wikipedia fetch failed: %s", exc)
        tickers = conn.execute(sa.select(sp500_universe.c.ticker)).scalars().all()
        if tickers:
            logger.warning("Using stale universe cache (%d tickers)", len(tickers))
            return list(tickers)
        raise

    df = tables[0]
    df = df.rename(columns={
        "Symbol": "ticker",
        "Security": "company_name",
        "GICS Sector": "gics_sector",
        "GICS Sub-Industry": "gics_sub_industry",
    })
    df["ticker"] = df["ticker"].str.replace(".", "-", regex=False)
    df = df[["ticker", "company_name", "gics_sector", "gics_sub_industry"]].copy()

    now = datetime.utcnow().isoformat(timespec="seconds")
    df["updated_at"] = now

    conn.execute(
        insert_or_replace(conn, sp500_universe),
        df[["ticker", "company_name", "gics_sector", "gics_sub_industry", "updated_at"]]
        .to_dict(orient="records"),
    )

    tickers = df["ticker"].tolist()
    deleted = conn.execute(
        sa.delete(sp500_universe).where(sp500_universe.c.ticker.notin_(tickers))
    ).rowcount
    if deleted:
        logger.info("Universe: removed %d tickers no longer in S&P 500", deleted)

    conn.commit()
    logger.info("Universe refreshed — %d S&P 500 tickers stored", len(tickers))
    return tickers


def load_benchmarks(conn: sa.engine.Connection, config: dict) -> list[str]:
    """Upsert benchmark tickers and return full list."""
    bm = config["universe"]["benchmark_tickers"]
    rows: list[dict] = []
    for ticker in bm["broad_market"]:
        rows.append({"ticker": ticker, "category": "broad_market"})
    for ticker in bm["sector_etfs"]:
        rows.append({"ticker": ticker, "category": "sector_etf"})
    for ticker in bm["other"]:
        rows.append({"ticker": ticker, "category": "other"})

    conn.execute(insert_or_replace(conn, benchmark_tickers), rows)
    conn.commit()
    all_tickers = [r["ticker"] for r in rows]
    logger.info("Benchmark tickers loaded: %d", len(all_tickers))
    return all_tickers


def get_all_tickers(conn: sa.engine.Connection, config: dict, force: bool = False) -> list[str]:
    """Return universe + benchmark tickers combined (deduplicated)."""
    universe = fetch_sp500(conn, config, force=force)
    benchmarks = load_benchmarks(conn, config)
    combined = list(dict.fromkeys(universe + benchmarks))
    logger.info("Total ticker universe: %d (SP500=%d, benchmarks=%d)",
                len(combined), len(universe), len(benchmarks))
    return combined
