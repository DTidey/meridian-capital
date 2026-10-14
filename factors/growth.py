"""Growth factor — 5 sub-factors, sector percentile ranked."""

import logging

import numpy as np
import pandas as pd

from factors._utils import pct_change, safe_div, sector_rank, value_and_prior

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

        with_revenue = tf[tf["revenue"].notna()]
        latest = with_revenue.iloc[-1] if not with_revenue.empty else tf.iloc[-1]
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
    """YoY % change: latest reported period vs the period 12 months earlier (by date)."""
    latest, prior = value_and_prior(df, col, 12)
    change = pct_change(latest, prior)
    return np.nan if change is None else change


_ACCEL_POINTS = 4  # YoY points used at most (8 values); at least 2 (4 values)


def _acceleration(df: pd.DataFrame, col: str) -> float:
    """Trend of YoY growth: least-squares slope per quarter over the latest four quarters.

    Each point is a quarter's growth on the same quarter a year earlier (so seasonality cancels),
    matched by calendar month; a missing quarter only drops the points that need it.
    """
    rows = df[df[col].notna()]
    if rows.empty:
        return np.nan
    ends = pd.to_datetime(rows["period_end"])
    values = {(d.year, d.month): v for d, v in zip(ends, rows[col], strict=True)}
    latest = ends.max()
    xs, ys = [], []
    for k in range(_ACCEL_POINTS):
        q = latest - pd.DateOffset(months=3 * k)
        p = q - pd.DateOffset(months=12)
        growth = pct_change(values.get((q.year, q.month)), values.get((p.year, p.month)))
        if growth is not None:
            xs.append(-k)
            ys.append(growth)
    if len(xs) < 2:
        return np.nan
    return float(np.polyfit(xs, ys, 1)[0])


def _empty_result(universe: pd.DataFrame) -> pd.DataFrame:
    tickers = universe["ticker"].tolist() if not universe.empty else []
    return pd.DataFrame(50.0, index=tickers, columns=COLS + ["growth_score"])
