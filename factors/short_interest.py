"""Short Interest factor — 3 sub-factors, sector percentile ranked.

LONG convention: lower short interest = higher score.
composite.py applies (100 - score) when computing the SHORT composite.
"""

import logging
from datetime import timedelta

import numpy as np
import pandas as pd

from factors._utils import sector_rank

logger = logging.getLogger(__name__)

COLS = ["si_pct_float", "si_days_to_cover", "si_change"]


def compute(data: dict[str, pd.DataFrame], config: dict) -> pd.DataFrame:
    """Return DataFrame indexed by ticker with short-interest sub-factor scores (0–100).

    Scores use LONG convention: declining / lower short interest = higher score.
    """
    universe = data["universe"]
    si = data["short_interest"]
    min_size = config.get("scoring", {}).get("min_sector_size", 5)

    if universe.empty:
        return _empty_result(universe)

    sectors = universe.set_index("ticker")["sector"]
    tickers = universe["ticker"].tolist()
    raw = pd.DataFrame(index=tickers, columns=COLS, dtype=float)

    if si.empty:
        return _default_50(universe)

    for ticker in tickers:
        ts = si[si["ticker"] == ticker].sort_values("date")
        if ts.empty:
            raw.loc[ticker] = np.nan
            continue

        latest = ts.iloc[-1]
        raw.loc[ticker, "si_pct_float"] = latest.get("short_pct_float")
        raw.loc[ticker, "si_days_to_cover"] = latest.get("short_ratio")

        # Change vs ~30 days ago
        ref_date = ts["date"].max()
        past = ts[ts["date"] <= ref_date - timedelta(days=28)]
        if past.empty:
            raw.loc[ticker, "si_change"] = np.nan
        else:
            prior_pct = past.iloc[-1]["short_pct_float"]
            cur_pct = latest["short_pct_float"]
            if prior_pct and prior_pct != 0 and cur_pct is not None:
                raw.loc[ticker, "si_change"] = (float(cur_pct) - float(prior_pct)) / abs(
                    float(prior_pct)
                )
            else:
                raw.loc[ticker, "si_change"] = np.nan

    raw = raw.astype(float)

    # Invert all sub-factors: lower short interest = higher rank for LONGS
    # We achieve this by negating before ranking (rank pct naturally puts highest last)
    scored = pd.DataFrame(index=raw.index)
    for col in COLS:
        # Negate: high short interest → most negative → lowest percentile rank
        scored[col] = sector_rank(-raw[col], sectors.reindex(raw.index), min_size)

    scored["short_interest_score"] = sector_rank(
        scored[COLS].mean(axis=1), sectors.reindex(scored.index), min_size
    )
    return scored


def _default_50(universe: pd.DataFrame) -> pd.DataFrame:
    tickers = universe["ticker"].tolist()
    return pd.DataFrame(50.0, index=tickers, columns=COLS + ["short_interest_score"])


def _empty_result(universe: pd.DataFrame) -> pd.DataFrame:
    tickers = universe["ticker"].tolist() if not universe.empty else []
    return pd.DataFrame(50.0, index=tickers, columns=COLS + ["short_interest_score"])
