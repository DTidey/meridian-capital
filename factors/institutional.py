"""Institutional Flow factor — 3 sub-factors, sector percentile ranked."""

import logging

import numpy as np
import pandas as pd

from factors._utils import sector_rank

logger = logging.getLogger(__name__)

COLS = ["inst_funds_holding", "inst_net_share_change", "inst_simultaneous_open"]


def compute(data: dict[str, pd.DataFrame], config: dict) -> pd.DataFrame:
    """Return DataFrame indexed by ticker with institutional sub-factor scores (0–100)."""
    universe    = data["universe"]
    institution = data["institutional"]
    min_size    = config.get("scoring", {}).get("min_sector_size", 5)

    if universe.empty:
        return _empty_result(universe)

    sectors = universe.set_index("ticker")["sector"]
    tickers = universe["ticker"].tolist()
    raw = pd.DataFrame(index=tickers, columns=COLS, dtype=float)

    if institution.empty:
        return _default_50(universe)

    # Latest report date per ticker
    latest_inst = (
        institution.sort_values("report_date")
        .groupby("ticker")
        .last()
    )

    for ticker in tickers:
        if ticker not in latest_inst.index:
            raw.loc[ticker] = np.nan
            continue

        row = latest_inst.loc[ticker]
        raw.loc[ticker, "inst_funds_holding"]    = row.get("funds_holding")
        raw.loc[ticker, "inst_net_share_change"] = row.get("net_share_change")
        new_pos = row.get("new_positions")
        # NaN = "no simultaneous opening detected" → neutral (50) after ranking
        raw.loc[ticker, "inst_simultaneous_open"] = 1.0 if (new_pos is not None and new_pos >= 3) else np.nan

    raw = raw.astype(float)

    scored = pd.DataFrame(index=raw.index)
    for col in COLS:
        scored[col] = sector_rank(raw[col], sectors.reindex(raw.index), min_size)

    scored["institutional_score"] = sector_rank(
        scored[COLS].mean(axis=1), sectors.reindex(scored.index), min_size
    )
    return scored


def _default_50(universe: pd.DataFrame) -> pd.DataFrame:
    tickers = universe["ticker"].tolist()
    return pd.DataFrame(50.0, index=tickers, columns=COLS + ["institutional_score"])


def _empty_result(universe: pd.DataFrame) -> pd.DataFrame:
    tickers = universe["ticker"].tolist() if not universe.empty else []
    return pd.DataFrame(50.0, index=tickers, columns=COLS + ["institutional_score"])
