"""Growth factor — 5 sub-factors, sector percentile ranked."""

import logging

import numpy as np
import pandas as pd

from factors._utils import pct_change, safe_div, sector_rank

logger = logging.getLogger(__name__)

COLS = [
    "grw_rev_yoy",
    "grw_earn_yoy",
    "grw_rev_accel",
    "grw_rd_intensity",
    "grw_fcf_yoy",
]


def compute(data: dict[str, pd.DataFrame], config: dict) -> pd.DataFrame:
    """Return DataFrame indexed by ticker with growth sub-factor scores (0–100)."""
    universe = data["universe"]
    funds = data["fundamentals"]
    min_size = config.get("scoring", {}).get("min_sector_size", 5)

    if universe.empty:
        return _empty_result(universe)

    sectors = universe.set_index("ticker")["sector"]
    tickers = universe["ticker"].tolist()
    raw = pd.DataFrame(index=tickers, columns=COLS, dtype=float)

    for ticker in tickers:
        tf = funds[funds["ticker"] == ticker].sort_values("period_end")
        if len(tf) < 2:
            raw.loc[ticker] = np.nan
            continue

        latest = tf.iloc[-1]
        raw.loc[ticker, "grw_rev_yoy"] = _yoy(tf, "revenue")
        raw.loc[ticker, "grw_earn_yoy"] = _yoy(tf, "net_income")
        raw.loc[ticker, "grw_rev_accel"] = _acceleration(tf, "revenue")
        raw.loc[ticker, "grw_rd_intensity"] = safe_div(
            latest.get("rd_expense"), latest.get("revenue")
        )
        raw.loc[ticker, "grw_fcf_yoy"] = _yoy(tf, "fcf")

    raw = raw.astype(float)

    scored = pd.DataFrame(index=raw.index)
    for col in COLS:
        scored[col] = sector_rank(raw[col], sectors.reindex(raw.index), min_size)

    scored["growth_score"] = sector_rank(
        scored[COLS].mean(axis=1), sectors.reindex(scored.index), min_size
    )
    return scored


def _yoy(df: pd.DataFrame, col: str) -> float:
    """YoY % change: compare latest period to the same period one year prior (4 quarters back)."""
    if len(df) < 5:
        return np.nan
    latest = df.iloc[-1][col]
    prior = df.iloc[-5][col]
    return pct_change(latest, prior)


def _acceleration(df: pd.DataFrame, col: str) -> float:
    """YoY growth acceleration: latest YoY minus YoY from 4 quarters ago."""
    if len(df) < 9:
        return np.nan
    current_yoy = pct_change(df.iloc[-1][col], df.iloc[-5][col])
    prior_yoy = pct_change(df.iloc[-5][col], df.iloc[-9][col])
    if current_yoy is None or prior_yoy is None:
        return np.nan
    return current_yoy - prior_yoy


def _empty_result(universe: pd.DataFrame) -> pd.DataFrame:
    tickers = universe["ticker"].tolist() if not universe.empty else []
    return pd.DataFrame(50.0, index=tickers, columns=COLS + ["growth_score"])
