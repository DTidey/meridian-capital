"""Weighted factor exposure across long and short books."""

import logging

import numpy as np
import pandas as pd
import sqlalchemy as sa

from factors.db import factor_scores as factor_scores_table

logger = logging.getLogger(__name__)

_FACTOR_COLS = [
    "momentum_score", "quality_score", "value_score", "revisions_score",
    "insider_score", "growth_score", "short_interest_score", "institutional_score",
]

_HISTORY_DATES = 60  # score dates to use for historical σ


def compute_exposures(
    positions_df: pd.DataFrame,
    factor_scores_df: pd.DataFrame,
) -> dict:
    """Return long/short/spread factor exposures.

    Returns:
        {
            "long":   {factor: weighted_avg},
            "short":  {factor: weighted_avg},
            "spread": {factor: long_avg - short_avg},
            "flags":  [factor names where spread > 1σ historical]
        }
    """
    long_pos  = positions_df[positions_df["direction"] == "LONG"]
    short_pos = positions_df[positions_df["direction"] == "SHORT"]

    long_exp  = _weighted_avg(long_pos,  factor_scores_df)
    short_exp = _weighted_avg(short_pos, factor_scores_df)

    spread = {f: long_exp.get(f, 50.0) - short_exp.get(f, 50.0) for f in _FACTOR_COLS}

    return {
        "long":   long_exp,
        "short":  short_exp,
        "spread": spread,
    }


def flag_unusual_exposures(
    spread: dict[str, float],
    conn: sa.engine.Connection,
    score_date: str,
) -> list[str]:
    """Return factor names where current spread exceeds 1σ of historical spreads."""
    hist = _historical_spreads(conn, score_date)
    flags = []
    for factor, current_spread in spread.items():
        if factor in hist and len(hist[factor]) >= 10:
            mean = np.mean(hist[factor])
            std  = np.std(hist[factor])
            if std > 0 and abs(current_spread - mean) > std:
                flags.append(factor)
    return flags


def _weighted_avg(book_df: pd.DataFrame, scores_df: pd.DataFrame) -> dict[str, float]:
    if book_df.empty:
        return {f: 50.0 for f in _FACTOR_COLS}

    merged = book_df[["ticker", "weight"]].merge(
        scores_df[["ticker"] + [c for c in _FACTOR_COLS if c in scores_df.columns]],
        on="ticker", how="left",
    )
    abs_weights = merged["weight"].abs()
    total_w = abs_weights.sum()
    if total_w == 0:
        return {f: 50.0 for f in _FACTOR_COLS}

    result = {}
    for factor in _FACTOR_COLS:
        if factor in merged.columns:
            vals = merged[factor].fillna(50.0)
            result[factor] = float((vals * abs_weights).sum() / total_w)
        else:
            result[factor] = 50.0
    return result


def _historical_spreads(
    conn: sa.engine.Connection,
    score_date: str,
) -> dict[str, list[float]]:
    """Load historical long-minus-short factor spreads from past score runs."""
    rows = conn.execute(
        sa.select(
            *[factor_scores_table.c[c] for c in _FACTOR_COLS
              if c in [col.name for col in factor_scores_table.columns]],
            factor_scores_table.c.direction,
        ).where(
            factor_scores_table.c.score_date <= score_date
        )
        .order_by(factor_scores_table.c.score_date.desc())
        .limit(5000)
    ).fetchall()

    if not rows:
        return {}

    available = [c for c in _FACTOR_COLS
                 if c in [col.name for col in factor_scores_table.columns]]
    col_names = available + ["direction"]
    df = pd.DataFrame(rows, columns=col_names)

    hist: dict[str, list[float]] = {f: [] for f in _FACTOR_COLS}
    long_df  = df[df["direction"] == "LONG"]
    short_df = df[df["direction"] == "SHORT"]
    for factor in available:
        l_mean = long_df[factor].mean()
        s_mean = short_df[factor].mean()
        if not (pd.isna(l_mean) or pd.isna(s_mean)):
            hist[factor].append(l_mean - s_mean)
    return hist
