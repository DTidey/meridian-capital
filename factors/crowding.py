"""Crowding detection — 60-day rolling factor return correlations.

Skips silently if fewer than 60 days of factor_scores history exist.
"""

import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd
import sqlalchemy as sa

from factors.db import factor_scores as factor_scores_table

logger = logging.getLogger(__name__)

_FACTOR_SCORE_COLS = {
    "momentum": "momentum_score",
    "quality": "quality_score",
    "value": "value_score",
    "revisions": "revisions_score",
    "insider": "insider_score",
    "growth": "growth_score",
    "short_interest": "short_interest_score",
    "institutional": "institutional_score",
}

_LONG_QUINTILE = 80
_SHORT_QUINTILE = 20


def detect(
    conn: sa.engine.Connection,
    prices: pd.DataFrame,
    score_date: str,
    crowding_config: dict,
) -> list[dict]:
    """Compute factor-return correlations and flag crowded pairs.

    Args:
        conn: Open SQLAlchemy connection (reads factor_scores history).
        prices: Daily prices DataFrame from loader (ticker, date, adj_close).
        score_date: ISO date of the current scoring run.
        crowding_config: config['scoring']['crowding'] section.

    Returns:
        List of dicts, one per factor pair, suitable for inserting into
        crowding_flags table.
    """
    window_days = crowding_config.get("window_days", 60)
    deviation_threshold = crowding_config.get("deviation_threshold", 0.40)
    baselines = crowding_config.get("baselines", {})

    cutoff = date.fromisoformat(score_date)
    start = (cutoff - timedelta(days=window_days + 5)).isoformat()

    # Load historical factor scores
    hist = _load_history(conn, start, score_date)

    if hist.empty:
        logger.info("Crowding: no factor score history — skipping detection")
        return []

    dates_available = hist["score_date"].nunique()
    if dates_available < window_days // 2:
        logger.info(
            "Crowding: only %d days of history (need ~%d) — skipping",
            dates_available,
            window_days,
        )
        return []

    # Compute daily factor long-minus-short returns
    factor_returns = _compute_factor_returns(hist, prices)
    if factor_returns.empty or len(factor_returns) < 10:
        logger.info("Crowding: insufficient factor return history — skipping")
        return []

    # Rolling correlation matrix over full window
    corr_matrix = factor_returns.corr()

    results = []
    computed_at = pd.Timestamp.now("UTC").isoformat()
    factors = list(_FACTOR_SCORE_COLS.keys())

    for i, fa in enumerate(factors):
        for fb in factors[i + 1 :]:
            if fa not in corr_matrix.index or fb not in corr_matrix.columns:
                continue
            rolling_corr = corr_matrix.loc[fa, fb]
            if np.isnan(rolling_corr):
                continue

            baseline_key = f"{fa}_{fb}"
            baseline_corr = baselines.get(baseline_key)
            deviation = (
                abs(rolling_corr - baseline_corr)
                if baseline_corr is not None
                else abs(rolling_corr)
            )
            flagged = 1 if deviation > deviation_threshold else 0

            results.append(
                {
                    "score_date": score_date,
                    "factor_a": fa,
                    "factor_b": fb,
                    "rolling_corr": float(rolling_corr),
                    "baseline_corr": float(baseline_corr) if baseline_corr is not None else None,
                    "deviation": float(deviation),
                    "flagged": flagged,
                    "computed_at": computed_at,
                }
            )

    n_flagged = sum(r["flagged"] for r in results)
    if n_flagged:
        logger.warning("Crowding: %d factor pair(s) flagged as crowded", n_flagged)
    else:
        logger.info("Crowding: no crowding flags raised")

    return results


def _load_history(
    conn: sa.engine.Connection,
    start: str,
    end: str,
) -> pd.DataFrame:
    rows = conn.execute(
        sa.select(
            factor_scores_table.c.ticker,
            factor_scores_table.c.score_date,
            factor_scores_table.c.momentum_score,
            factor_scores_table.c.quality_score,
            factor_scores_table.c.value_score,
            factor_scores_table.c.revisions_score,
            factor_scores_table.c.insider_score,
            factor_scores_table.c.growth_score,
            factor_scores_table.c.short_interest_score,
            factor_scores_table.c.institutional_score,
        ).where(
            (factor_scores_table.c.score_date >= start) & (factor_scores_table.c.score_date <= end)
        )
    ).fetchall()

    cols = ["ticker", "score_date"] + list(_FACTOR_SCORE_COLS.values())
    return pd.DataFrame(rows, columns=cols)


def _compute_factor_returns(
    hist: pd.DataFrame,
    prices: pd.DataFrame,
) -> pd.DataFrame:
    """Compute daily long-minus-short factor return series."""
    if prices.empty:
        return pd.DataFrame()

    # Pivot prices: date × ticker → adj_close
    price_wide = prices.pivot(index="date", columns="ticker", values="adj_close").sort_index()

    factor_ret_series = {}

    for factor_name, score_col in _FACTOR_SCORE_COLS.items():
        daily_returns = []

        for score_date, day_group in hist.groupby("score_date"):
            day_group = day_group.set_index("ticker")
            if score_col not in day_group.columns:
                continue

            long_tickers = day_group.index[day_group[score_col] >= _LONG_QUINTILE].tolist()
            short_tickers = day_group.index[day_group[score_col] <= _SHORT_QUINTILE].tolist()

            # Find next trading day's return
            date_ts = pd.Timestamp(score_date)
            future = price_wide[price_wide.index > date_ts]
            if future.empty:
                continue
            next_day = future.index[0]

            prev_day_prices = price_wide.loc[price_wide.index <= date_ts]
            if prev_day_prices.empty:
                continue
            prev_day = prev_day_prices.index[-1]

            def _mean_ret(tickers, _prev=prev_day, _next=next_day):
                cols = [t for t in tickers if t in price_wide.columns]
                if not cols:
                    return np.nan
                ret = price_wide.loc[_next, cols] / price_wide.loc[_prev, cols] - 1
                return float(ret.mean())

            long_ret = _mean_ret(long_tickers)
            short_ret = _mean_ret(short_tickers)

            if not np.isnan(long_ret) and not np.isnan(short_ret):
                daily_returns.append({"date": score_date, "ret": long_ret - short_ret})

        if daily_returns:
            s = pd.DataFrame(daily_returns).set_index("date")["ret"]
            factor_ret_series[factor_name] = s

    if not factor_ret_series:
        return pd.DataFrame()

    return pd.DataFrame(factor_ret_series)
