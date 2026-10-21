"""Estimate Revisions factor — 3 sub-factors, sector percentile ranked.

Percent change of Yahoo's +1y consensus EPS against its own value 30, 60 and 90 days ago, for
the same fiscal year, from the latest fresh earningsTrend snapshot.
"""

import logging

import pandas as pd

from factors._utils import GUARD_FLOOR, guarded_current, latest_trends, sector_rank

logger = logging.getLogger(__name__)

COLS = ["rev_30d", "rev_60d", "rev_90d"]

_WINDOWS = {"rev_30d": 30, "rev_60d": 60, "rev_90d": 90}


def compute(data: dict[str, pd.DataFrame], config: dict) -> pd.DataFrame:
    """Return DataFrame indexed by ticker with revision sub-factor scores (0–100).

    A ticker without a fresh +1y trend snapshot, or without a window's past value, scores a
    neutral 50 on that sub-factor.
    """
    universe = data["universe"]
    trends = data["estimate_trends"]
    min_size = config.get("scoring", {}).get("min_sector_size", 5)

    if universe.empty:
        return _empty_result(universe)

    sectors = universe.set_index("ticker")["sector"]
    tickers = universe["ticker"].tolist()

    if trends.empty:
        logger.warning("Revisions: no estimate trend snapshots — all tickers neutral (50.0)")
        return _empty_result(universe)

    raw = _raw_revisions(latest_trends(trends).reindex(tickers))

    scored = pd.DataFrame(index=raw.index)
    for col in COLS:
        scored[col] = sector_rank(raw[col], sectors.reindex(raw.index), min_size)

    scored["revisions_score"] = sector_rank(
        scored[COLS].mean(axis=1), sectors.reindex(scored.index), min_size
    )
    return scored


def _raw_revisions(latest: pd.DataFrame) -> pd.DataFrame:
    """(g - days_N) / max(|days_N|, 0.10) per window, g the guarded current; NaN when missing."""
    guarded = pd.Series(
        [guarded_current(row) for _, row in latest.iterrows()], index=latest.index, dtype=float
    )
    _log_guard(latest, guarded)
    raw = pd.DataFrame(index=latest.index, columns=COLS, dtype=float)
    for col, window in _WINDOWS.items():
        prior = latest[f"days_{window}"].astype(float)
        raw[col] = (guarded - prior) / prior.abs().clip(lower=GUARD_FLOOR)
    return raw


def _log_guard(latest: pd.DataFrame, guarded: pd.Series) -> None:
    replaced = guarded.index[guarded.ne(latest["current"].astype(float)) & guarded.notna()]
    names = f" ({', '.join(replaced)})" if len(replaced) else ""
    logger.info(
        "Revisions: glitch guard used the 7-day-ago consensus for %d ticker%s%s",
        len(replaced),
        "" if len(replaced) == 1 else "s",
        names,
    )


def _empty_result(universe: pd.DataFrame) -> pd.DataFrame:
    tickers = universe["ticker"].tolist() if not universe.empty else []
    return pd.DataFrame(50.0, index=tickers, columns=COLS + ["revisions_score"])
