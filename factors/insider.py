"""Insider Activity factor — 2 scored sub-factors, sector percentile ranked.

Transaction filter: only open-market codes P (purchase) and S (sale) counted.
This is pre-applied by loader.py (is_open_market == 1 filter).

CEO/CFO purchases/sales are weighted 3× versus other insiders.
No data → sector median (50.0).
"""

import logging

import numpy as np
import pandas as pd

from factors._utils import sector_rank

logger = logging.getLogger(__name__)

COLS = ["ins_net_flow", "ins_cluster_flag"]

_CEO_CFO_WEIGHT = 3.0


def compute(data: dict[str, pd.DataFrame], config: dict) -> pd.DataFrame:
    """Return DataFrame indexed by ticker with insider sub-factor scores (0–100)."""
    universe = data["universe"]
    ins_txns = data["insider_txns"]
    ins_flags = data["insider_clusters"]
    min_size = config.get("scoring", {}).get("min_sector_size", 5)

    if universe.empty:
        return _empty_result(universe)

    sectors = universe.set_index("ticker")["sector"]
    tickers = universe["ticker"].tolist()
    raw = pd.DataFrame(index=tickers, columns=COLS, dtype=float)

    # Tickers with cluster flags (within 90-day window already filtered by loader)
    flagged_tickers = set(ins_flags["ticker"].unique()) if not ins_flags.empty else set()

    for ticker in tickers:
        # NaN = "no cluster detected" → treated as neutral (50) after ranking
        raw.loc[ticker, "ins_cluster_flag"] = 1.0 if ticker in flagged_tickers else np.nan

        if ins_txns.empty:
            raw.loc[ticker, "ins_net_flow"] = np.nan
            continue

        tt = ins_txns[ins_txns["ticker"] == ticker]
        if tt.empty:
            raw.loc[ticker, "ins_net_flow"] = np.nan
            continue

        net_flow = 0.0
        for _, row in tt.iterrows():
            shares = float(row.get("shares") or 0)
            price = float(row.get("price") or 0)
            code = str(row.get("transaction_code") or "")
            is_exec = int(row.get("is_ceo_cfo") or 0)
            weight = _CEO_CFO_WEIGHT if is_exec else 1.0

            dollar = shares * price * weight
            if code == "P":
                net_flow += dollar
            elif code == "S":
                net_flow -= dollar

        raw.loc[ticker, "ins_net_flow"] = net_flow

    raw = raw.astype(float)

    scored = pd.DataFrame(index=raw.index)
    for col in COLS:
        scored[col] = sector_rank(raw[col], sectors.reindex(raw.index), min_size)

    scored["insider_score"] = sector_rank(
        scored[COLS].mean(axis=1), sectors.reindex(scored.index), min_size
    )
    return scored


def _empty_result(universe: pd.DataFrame) -> pd.DataFrame:
    tickers = universe["ticker"].tolist() if not universe.empty else []
    return pd.DataFrame(50.0, index=tickers, columns=COLS + ["insider_score"])
