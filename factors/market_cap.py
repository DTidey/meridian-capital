"""Market cap from Yahoo's stored marketCap, with a one-snapshot glitch guard (packet 42).

Shares are derived as market cap / our close on the snapshot date. Our closes are
split-adjusted retroactively, so a derived count stays comparable across a split.
"""

from __future__ import annotations

import logging

import pandas as pd

from factors.ttm import num

logger = logging.getLogger(__name__)

GUARD_MAX_RATIO = 1.5  # derived shares moving more than this between snapshots is a glitch


def shares_from_snapshots(snapshots: list[tuple[float, float]]) -> float | None:
    """Effective shares from (market_cap, close) snapshots, newest first.

    Uses the two newest usable snapshots: the latest unless its share count is more than 1.5x
    away from the previous one, in which case the previous one is kept.
    """
    derived = [m / c for m, c in snapshots if m and c and m > 0 and c > 0][:2]
    if not derived:
        return None
    if (
        len(derived) == 2
        and max(derived[0] / derived[1], derived[1] / derived[0]) > GUARD_MAX_RATIO
    ):
        return derived[1]
    return derived[0]


def balance_sheet_shares(quarters: pd.DataFrame) -> float | None:
    """Shares from the latest quarter with a balance sheet (fallback without snapshots)."""
    with_bs = quarters[quarters["total_assets"].notna()].sort_values("period_end")
    if with_bs.empty:
        return None
    shares = num(with_bs["shares_outstanding"].iloc[-1])
    return shares if shares and shares > 0 else None


def market_caps(
    prices: pd.DataFrame, snapshots: pd.DataFrame | None, fundamentals: pd.DataFrame
) -> pd.Series:
    """Market cap per ticker: effective shares x latest close (NaN when unknown)."""
    if prices.empty:
        return pd.Series(dtype=float)
    prices = prices.sort_values("date")
    closes = {t: g.set_index("date")["close"] for t, g in prices.groupby("ticker")}
    snaps = _snapshots_by_ticker(snapshots)
    funds = dict(list(fundamentals.groupby("ticker"))) if not fundamentals.empty else {}
    result, fallbacks = {}, 0
    for ticker, close in closes.items():
        shares = shares_from_snapshots(_with_closes(snaps.get(ticker, []), close))
        if shares is None and ticker in funds:
            shares = balance_sheet_shares(funds[ticker])
            fallbacks += shares is not None
        result[ticker] = shares * float(close.iloc[-1]) if shares else float("nan")
    if fallbacks:
        logger.info("Market cap: %d tickers fell back to balance-sheet shares", fallbacks)
    return pd.Series(result, dtype=float)


def _snapshots_by_ticker(snapshots: pd.DataFrame | None) -> dict[str, list[tuple]]:
    """{ticker: [(date, market_cap), ...] newest first}."""
    if snapshots is None or snapshots.empty:
        return {}
    ordered = snapshots.sort_values("date", ascending=False)
    return {
        t: list(zip(g["date"], g["market_cap"], strict=True)) for t, g in ordered.groupby("ticker")
    }


def _with_closes(snaps: list[tuple], close: pd.Series) -> list[tuple[float, float]]:
    """(market_cap, close on the snapshot date or the nearest earlier bar)."""
    paired = []
    for day, mcap in snaps:
        earlier = close.loc[: pd.Timestamp(day)]
        paired.append((num(mcap), num(earlier.iloc[-1]) if not earlier.empty else None))
    return paired
