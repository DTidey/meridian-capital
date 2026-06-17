"""Estimate Revisions factor — 3 sub-factors, sector percentile ranked.

Degenerate until ~30 days of analyst_estimates snapshots accumulate.
"""

import logging
from datetime import timedelta

import numpy as np
import pandas as pd

from factors._utils import sector_rank

logger = logging.getLogger(__name__)

COLS = ["rev_30d", "rev_60d", "rev_90d"]

_WINDOWS = {"rev_30d": 30, "rev_60d": 60, "rev_90d": 90}
_DEGENERATE_THRESHOLD = 30  # days of history required before factor is live


def compute(data: dict[str, pd.DataFrame], config: dict) -> pd.DataFrame:
    """Return DataFrame indexed by ticker with revision sub-factor scores (0–100).

    If fewer than _DEGENERATE_THRESHOLD days of snapshots exist for a ticker,
    that ticker's sub-factors default to 50.0 (neutral).
    """
    universe = data["universe"]
    estimates = data["estimates"]
    min_size = config.get("scoring", {}).get("min_sector_size", 5)

    if universe.empty:
        return _empty_result(universe)

    sectors = universe.set_index("ticker")["sector"]
    tickers = universe["ticker"].tolist()
    raw = pd.DataFrame(index=tickers, columns=COLS, dtype=float)

    if estimates.empty:
        logger.warning("Revisions: no estimate data — all tickers degenerate (50.0)")
        return _default_50(universe)

    # Date of latest snapshot (used as reference)
    ref_date = estimates["date"].max()

    degenerate_count = 0

    for ticker in tickers:
        te = estimates[estimates["ticker"] == ticker].sort_values("date")
        if te.empty:
            raw.loc[ticker] = np.nan
            degenerate_count += 1
            continue

        history_days = (te["date"].max() - te["date"].min()).days

        if history_days < _DEGENERATE_THRESHOLD:
            raw.loc[ticker] = np.nan
            degenerate_count += 1
            continue

        latest_eps = te.iloc[-1]["eps_estimate_fwd"]

        computed_any = False
        for col, window in _WINDOWS.items():
            cutoff = ref_date - timedelta(days=window)
            past = te[te["date"] <= cutoff]
            if past.empty:
                raw.loc[ticker, col] = np.nan
            else:
                prior_eps = past.iloc[-1]["eps_estimate_fwd"]
                if prior_eps is None or np.isnan(float(prior_eps)):
                    raw.loc[ticker, col] = np.nan
                else:
                    raw.loc[ticker, col] = float(latest_eps) - float(prior_eps)
                    computed_any = True

        if not computed_any:
            degenerate_count += 1

    if degenerate_count > len(tickers) * 0.5:
        logger.warning(
            "Revisions: %d/%d tickers degenerate (< %d days of history)",
            degenerate_count,
            len(tickers),
            _DEGENERATE_THRESHOLD,
        )

    raw = raw.astype(float)

    scored = pd.DataFrame(index=raw.index)
    for col in COLS:
        scored[col] = sector_rank(raw[col], sectors.reindex(raw.index), min_size)

    scored["revisions_score"] = sector_rank(
        scored[COLS].mean(axis=1), sectors.reindex(scored.index), min_size
    )
    return scored


def _default_50(universe: pd.DataFrame) -> pd.DataFrame:
    tickers = universe["ticker"].tolist()
    return pd.DataFrame(50.0, index=tickers, columns=COLS + ["revisions_score"])


def _empty_result(universe: pd.DataFrame) -> pd.DataFrame:
    tickers = universe["ticker"].tolist() if not universe.empty else []
    return pd.DataFrame(50.0, index=tickers, columns=COLS + ["revisions_score"])
