"""Load Layer 1 data into DataFrames for the scoring engine."""

import logging
from datetime import date, timedelta

import pandas as pd
import sqlalchemy as sa

from data.db import (
    analyst_estimates,
    daily_prices,
    fundamentals,
    insider_cluster_flags,
    insider_transactions,
    institutional_summary,
    short_interest,
    sp500_universe,
)

logger = logging.getLogger(__name__)

_PRICE_LOOKBACK_DAYS = 800  # ~3 years of trading days with buffer
_FUND_QUARTERS = 12
_SI_LOOKBACK_DAYS = 90
_EST_LOOKBACK_DAYS = 90
_INSIDER_DAYS = 90


def load_scoring_data(
    conn: sa.engine.Connection,
    config: dict,
    score_date: str,
) -> dict[str, pd.DataFrame]:
    """Return all Layer 1 data needed for factor scoring.

    Args:
        conn: Open SQLAlchemy connection.
        config: Parsed config.yaml.
        score_date: ISO date string (YYYY-MM-DD) for the scoring run.

    Returns:
        Dict with keys: universe, prices, fundamentals, short_interest,
        estimates, insider_txns, insider_clusters, institutional, vix.
    """
    cutoff = date.fromisoformat(score_date)

    universe = _load_universe(conn)
    prices = _load_prices(conn, cutoff)
    funds = _load_fundamentals(conn, cutoff)
    si = _load_short_interest(conn, cutoff)
    estimates = _load_estimates(conn, cutoff)
    ins_txns = _load_insider_txns(conn, cutoff)
    ins_flags = _load_insider_flags(conn, cutoff)
    institution = _load_institutional(conn, cutoff)
    vix = _load_vix(conn, cutoff)

    logger.debug(
        "Loaded: %d universe, %d price rows, %d fund rows",
        len(universe),
        len(prices),
        len(funds),
    )

    return {
        "universe": universe,
        "prices": prices,
        "fundamentals": funds,
        "short_interest": si,
        "estimates": estimates,
        "insider_txns": ins_txns,
        "insider_clusters": ins_flags,
        "institutional": institution,
        "vix": vix,
    }


# ---------------------------------------------------------------------------
# Private loaders
# ---------------------------------------------------------------------------


def _load_universe(conn: sa.engine.Connection) -> pd.DataFrame:
    rows = conn.execute(
        sa.select(
            sp500_universe.c.ticker,
            sp500_universe.c.company_name,
            sp500_universe.c.gics_sector,
            sp500_universe.c.gics_sub_industry,
        )
    ).fetchall()
    return pd.DataFrame(rows, columns=["ticker", "company_name", "sector", "sub_industry"])


def _load_prices(conn: sa.engine.Connection, cutoff: date) -> pd.DataFrame:
    start = (cutoff - timedelta(days=_PRICE_LOOKBACK_DAYS)).isoformat()
    rows = conn.execute(
        sa.select(
            daily_prices.c.ticker,
            daily_prices.c.date,
            daily_prices.c.adj_close,
            daily_prices.c.close,
            daily_prices.c.volume,
        )
        .where((daily_prices.c.date >= start) & (daily_prices.c.date <= score_date_str(cutoff)))
        .order_by(daily_prices.c.ticker, daily_prices.c.date)
    ).fetchall()
    df = pd.DataFrame(rows, columns=["ticker", "date", "adj_close", "close", "volume"])
    df["date"] = pd.to_datetime(df["date"])
    return df


def _load_fundamentals(conn: sa.engine.Connection, cutoff: date) -> pd.DataFrame:
    rows = conn.execute(
        sa.select(fundamentals)
        .where(
            (fundamentals.c.period_type == "quarterly")
            & (fundamentals.c.period_end <= score_date_str(cutoff))
        )
        .order_by(fundamentals.c.ticker, fundamentals.c.period_end)
    ).fetchall()
    df = pd.DataFrame(rows, columns=[c.name for c in fundamentals.columns])
    df["period_end"] = pd.to_datetime(df["period_end"])
    return df


def _load_short_interest(conn: sa.engine.Connection, cutoff: date) -> pd.DataFrame:
    start = (cutoff - timedelta(days=_SI_LOOKBACK_DAYS)).isoformat()
    rows = conn.execute(
        sa.select(
            short_interest.c.ticker,
            short_interest.c.date,
            short_interest.c.short_pct_float,
            short_interest.c.short_ratio,
            short_interest.c.shares_short,
        )
        .where((short_interest.c.date >= start) & (short_interest.c.date <= score_date_str(cutoff)))
        .order_by(short_interest.c.ticker, short_interest.c.date)
    ).fetchall()
    df = pd.DataFrame(
        rows, columns=["ticker", "date", "short_pct_float", "short_ratio", "shares_short"]
    )
    df["date"] = pd.to_datetime(df["date"])
    return df


def _load_estimates(conn: sa.engine.Connection, cutoff: date) -> pd.DataFrame:
    start = (cutoff - timedelta(days=_EST_LOOKBACK_DAYS)).isoformat()
    rows = conn.execute(
        sa.select(
            analyst_estimates.c.ticker,
            analyst_estimates.c.date,
            analyst_estimates.c.eps_estimate_fwd,
            analyst_estimates.c.price_target,
            analyst_estimates.c.num_analysts,
        )
        .where(
            (analyst_estimates.c.date >= start)
            & (analyst_estimates.c.date <= score_date_str(cutoff))
        )
        .order_by(analyst_estimates.c.ticker, analyst_estimates.c.date)
    ).fetchall()
    df = pd.DataFrame(
        rows, columns=["ticker", "date", "eps_estimate_fwd", "price_target", "num_analysts"]
    )
    df["date"] = pd.to_datetime(df["date"])
    return df


def _load_insider_txns(conn: sa.engine.Connection, cutoff: date) -> pd.DataFrame:
    start = (cutoff - timedelta(days=_INSIDER_DAYS)).isoformat()
    rows = conn.execute(
        sa.select(
            insider_transactions.c.ticker,
            insider_transactions.c.insider_name,
            insider_transactions.c.insider_title,
            insider_transactions.c.transaction_code,
            insider_transactions.c.shares,
            insider_transactions.c.price,
            insider_transactions.c.date,
            insider_transactions.c.is_open_market,
            insider_transactions.c.is_ceo_cfo,
        )
        .where(
            (insider_transactions.c.date >= start)
            & (insider_transactions.c.date <= score_date_str(cutoff))
            & (insider_transactions.c.is_open_market == 1)
        )
        .order_by(insider_transactions.c.ticker, insider_transactions.c.date)
    ).fetchall()
    cols = [
        "ticker",
        "insider_name",
        "insider_title",
        "transaction_code",
        "shares",
        "price",
        "date",
        "is_open_market",
        "is_ceo_cfo",
    ]
    df = pd.DataFrame(rows, columns=cols)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"].astype(str).str[:10])
    return df


def _load_insider_flags(conn: sa.engine.Connection, cutoff: date) -> pd.DataFrame:
    start = (cutoff - timedelta(days=_INSIDER_DAYS)).isoformat()
    rows = conn.execute(
        sa.select(
            insider_cluster_flags.c.ticker,
            insider_cluster_flags.c.window_start,
            insider_cluster_flags.c.window_end,
            insider_cluster_flags.c.insider_count,
        ).where(
            (insider_cluster_flags.c.window_start >= start)
            & (insider_cluster_flags.c.window_start <= score_date_str(cutoff))
        )
    ).fetchall()
    df = pd.DataFrame(rows, columns=["ticker", "window_start", "window_end", "insider_count"])
    return df


def _load_institutional(conn: sa.engine.Connection, cutoff: date) -> pd.DataFrame:
    rows = conn.execute(
        sa.select(
            institutional_summary.c.ticker,
            institutional_summary.c.report_date,
            institutional_summary.c.funds_holding,
            institutional_summary.c.net_share_change,
            institutional_summary.c.new_positions,
        )
        .where(institutional_summary.c.report_date <= score_date_str(cutoff))
        .order_by(institutional_summary.c.ticker, institutional_summary.c.report_date)
    ).fetchall()
    df = pd.DataFrame(
        rows,
        columns=["ticker", "report_date", "funds_holding", "net_share_change", "new_positions"],
    )
    if not df.empty:
        df["report_date"] = pd.to_datetime(df["report_date"])
    return df


def _load_vix(conn: sa.engine.Connection, cutoff: date) -> pd.DataFrame:
    start = (cutoff - timedelta(days=5)).isoformat()
    rows = conn.execute(
        sa.select(
            daily_prices.c.date,
            daily_prices.c.close,
        )
        .where(
            (daily_prices.c.ticker == "^VIX")
            & (daily_prices.c.date >= start)
            & (daily_prices.c.date <= score_date_str(cutoff))
        )
        .order_by(daily_prices.c.date.desc())
    ).fetchall()
    df = pd.DataFrame(rows, columns=["date", "close"])
    return df


def score_date_str(d: date) -> str:
    return d.isoformat()
