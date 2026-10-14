"""Shared utilities for factor scoring."""

import numpy as np
import pandas as pd


def sector_rank(series: pd.Series, sectors: pd.Series, min_sector_size: int = 5) -> pd.Series:
    """Rank values within GICS sector, returning 0–100 percentile scores.

    Tickers with NaN raw values receive 50.0 (sector median).
    Sectors smaller than min_sector_size fall back to universe-wide rank.
    """
    result = pd.Series(np.nan, index=series.index, dtype=float)

    sector_counts = sectors.value_counts()
    small_sectors = set(sector_counts[sector_counts < min_sector_size].index)

    # Universe-wide rank for small sectors (and NaN values handled below)
    universe_mask = sectors.isin(small_sectors)
    if universe_mask.any():
        valid = series[universe_mask].notna()
        result[universe_mask & valid] = (
            series[universe_mask & valid].rank(pct=True, na_option="keep") * 100
        )

    # Within-sector rank for normal sectors
    for sector, grp_idx in series.groupby(sectors).groups.items():
        if sector in small_sectors:
            continue
        grp = series.loc[grp_idx]
        ranked = grp.rank(pct=True, na_option="keep") * 100
        result.loc[grp_idx] = ranked

    # Tickers with NaN raw value → sector median (50)
    result = result.fillna(50.0)
    return result


def safe_div(num, denom, default=None):
    """Return num/denom, or default when denom is zero/NaN."""
    try:
        if denom is None or denom == 0 or np.isnan(float(denom)):
            return default
        if num is None or np.isnan(float(num)):
            return default
        return float(num) / float(denom)
    except (TypeError, ValueError):
        return default


def pct_change(current, prior, default=None):
    """Percentage change: (current - prior) / abs(prior)."""
    if current is None or prior is None:
        return default
    try:
        c, p = float(current), float(prior)
        if np.isnan(c) or np.isnan(p) or p == 0:
            return default
        return (c - p) / abs(p)
    except (TypeError, ValueError):
        return default


def value_and_prior(df: pd.DataFrame, col: str, months: int) -> tuple[float | None, float | None]:
    """(latest reported value, value exactly `months` calendar months earlier) for a column.

    Matched by calendar year and month rather than row position, so a missing quarter gives
    None instead of a comparison with the wrong period, and an empty newest row is skipped.
    """
    rows = df[df[col].notna()]
    if rows.empty:
        return None, None
    ends = pd.to_datetime(rows["period_end"])
    latest_end = ends.max()
    target = latest_end - pd.DateOffset(months=months)
    latest = rows[col].iloc[ends.values.argmax()]  # positional: index labels may repeat
    prior = rows.loc[(ends.dt.year == target.year) & (ends.dt.month == target.month), col]
    return float(latest), (float(prior.iloc[0]) if not prior.empty else None)
