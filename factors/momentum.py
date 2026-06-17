"""Momentum factor — 6 sub-factors, sector percentile ranked."""

import logging

import numpy as np
import pandas as pd

from factors._utils import sector_rank

logger = logging.getLogger(__name__)

# Minimum trading days required to score a ticker
_MIN_HISTORY = 252

# Column names
COLS = [
    "mom_12_1",
    "mom_6m",
    "mom_3m",
    "mom_accel",
    "mom_52w_high",
    "mom_rel_strength",
]


def compute(data: dict[str, pd.DataFrame], config: dict) -> pd.DataFrame:
    """Return DataFrame indexed by ticker with momentum sub-factor scores (0–100).

    Args:
        data: Output of loader.load_scoring_data().
        config: Parsed config.yaml.

    Returns:
        DataFrame with columns COLS + ['momentum_score'], index = ticker.
    """
    prices   = data["prices"]
    universe = data["universe"]
    etf_map  = config.get("scoring", {}).get("sector_etf_map", {})
    min_size = config.get("scoring", {}).get("min_sector_size", 5)

    if prices.empty or universe.empty:
        return _empty_result(universe)

    # Pivot to wide: date × ticker adj_close
    wide = prices.pivot(index="date", columns="ticker", values="adj_close").sort_index()

    tickers = universe["ticker"].tolist()
    sectors = universe.set_index("ticker")["sector"]

    raw = _compute_raw(wide, tickers, etf_map, sectors)

    # Rank each sub-factor within sector
    scored = pd.DataFrame(index=raw.index)
    for col in COLS:
        if col in raw.columns:
            scored[col] = sector_rank(raw[col], sectors.reindex(raw.index), min_size)
        else:
            scored[col] = 50.0

    scored["momentum_score"] = sector_rank(
        scored[COLS].mean(axis=1), sectors.reindex(scored.index), min_size
    )

    return scored


def _compute_raw(wide: pd.DataFrame, tickers: list, etf_map: dict, sectors: pd.Series) -> pd.DataFrame:
    """Compute raw (un-ranked) momentum values."""
    # Restrict to universe tickers present in price data
    available = [t for t in tickers if t in wide.columns]
    missing   = len(tickers) - len(available)
    if missing:
        logger.debug("Momentum: %d tickers missing price history", missing)

    prices = wide[available].copy()
    n = len(prices)

    raw = pd.DataFrame(index=available)

    for ticker in available:
        s = prices[ticker].dropna()
        if len(s) < _MIN_HISTORY:
            # Insufficient history — all sub-factors NaN (→ 50 after ranking)
            for col in COLS:
                raw.loc[ticker, col] = np.nan
            continue

        latest = s.iloc[-1]
        # 12-1 month: skip most recent 21 trading days
        idx_12m = max(0, len(s) - 252)
        idx_1m  = max(0, len(s) - 21)
        raw.loc[ticker, "mom_12_1"] = _ret(s.iloc[idx_12m], s.iloc[idx_1m])

        # 6-month
        idx_6m = max(0, len(s) - 126)
        raw.loc[ticker, "mom_6m"] = _ret(s.iloc[idx_6m], latest)

        # 3-month
        idx_3m = max(0, len(s) - 63)
        raw.loc[ticker, "mom_3m"] = _ret(s.iloc[idx_3m], latest)

        # Acceleration: recent 3m minus prior 3m
        if len(s) >= 126:
            prior_3m = _ret(s.iloc[idx_6m], s.iloc[idx_3m])
            raw.loc[ticker, "mom_accel"] = raw.loc[ticker, "mom_3m"] - prior_3m
        else:
            raw.loc[ticker, "mom_accel"] = np.nan

        # 52-week high proximity
        if len(s) >= 252:
            high_52w = s.iloc[-252:].max()
            raw.loc[ticker, "mom_52w_high"] = latest / high_52w if high_52w > 0 else np.nan
        else:
            raw.loc[ticker, "mom_52w_high"] = np.nan

        # Relative strength vs sector ETF
        sector = sectors.get(ticker)
        etf    = etf_map.get(sector) if sector else None
        if etf and etf in wide.columns:
            etf_s = wide[etf].dropna()
            if len(etf_s) >= 126:
                etf_idx = max(0, len(etf_s) - 126)
                etf_ret = _ret(etf_s.iloc[etf_idx], etf_s.iloc[-1])
                raw.loc[ticker, "mom_rel_strength"] = raw.loc[ticker, "mom_6m"] - etf_ret
            else:
                raw.loc[ticker, "mom_rel_strength"] = np.nan
        else:
            raw.loc[ticker, "mom_rel_strength"] = np.nan

    return raw


def _ret(start_price: float, end_price: float) -> float:
    if start_price and start_price != 0:
        return (end_price - start_price) / start_price
    return np.nan


def _empty_result(universe: pd.DataFrame) -> pd.DataFrame:
    tickers = universe["ticker"].tolist() if not universe.empty else []
    df = pd.DataFrame(50.0, index=tickers, columns=COLS + ["momentum_score"])
    return df
