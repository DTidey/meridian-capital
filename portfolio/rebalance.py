"""Diff current vs target to generate a trade list."""

import logging
import math
from datetime import UTC, datetime

import pandas as pd
import sqlalchemy as sa

from portfolio.db import position_approvals
from portfolio.limits import position_cap, sector_gross_cap
from portfolio.transaction_costs import estimate_cost

logger = logging.getLogger(__name__)

_HOLD_THRESHOLD = 1.0  # shares delta below this → HOLD


def generate_trades(
    current: pd.DataFrame,
    target: pd.DataFrame,
    prices: dict[str, pd.DataFrame],
    config: dict,
    conn: sa.engine.Connection,
    score_date: str,
    commit: bool = True,
) -> pd.DataFrame:
    """Return ordered trade list; optionally commit to DB.

    Returns DataFrame with columns:
        ticker, action, current_shares, target_shares, delta_shares,
        estimated_cost_usd, priority, direction
    """
    pcfg = config.get("portfolio", {})
    nav = float(pcfg.get("nav_usd", 10_000_000))
    turnover = float(pcfg.get("turnover_budget_pct", 0.30))

    if not current.empty and current["ticker"].duplicated().any():
        # Last-wins would size the trade from one lot only (e.g. cover 5 of a -15 short).
        dupes = sorted(current.loc[current["ticker"].duplicated(), "ticker"].unique())
        raise ValueError(f"duplicate tickers in current positions: {dupes}")

    cur_map = (
        current.set_index("ticker")["shares"].to_dict()
        if not current.empty and "shares" in current.columns
        else {}
    )
    tgt_cols = [c for c in ("shares", "direction", "combined_score", "sector") if c in target]
    tgt_map = (
        target.set_index("ticker")[tgt_cols].to_dict(orient="index") if not target.empty else {}
    )

    all_tickers = set(cur_map) | set(tgt_map)
    price_of = {t: _last_close(prices.get(t, pd.DataFrame())) for t in all_tickers}
    planned = _planned_shares(tgt_map, price_of, nav, config)
    rows = []
    for ticker in all_tickers:
        cur_shares = cur_map.get(ticker, 0.0)
        tgt_info = tgt_map.get(ticker, {})
        direction = tgt_info.get("direction") or _infer_direction(cur_shares)
        price_df = prices.get(ticker, pd.DataFrame())
        price = price_of[ticker]
        tgt_shares = planned.get(ticker, 0)
        delta = tgt_shares - cur_shares
        action = map_action(cur_shares, tgt_shares, delta)
        cost = (
            estimate_cost(ticker, abs(delta), price, price_df, config)
            if abs(delta) >= _HOLD_THRESHOLD
            else 0.0
        )

        rows.append(
            {
                "ticker": ticker,
                "action": action,
                "current_shares": cur_shares,
                "target_shares": tgt_shares,
                "delta_shares": delta,
                "estimated_cost_usd": cost,
                "direction": direction,
                "combined_score": tgt_info.get("combined_score"),
                "price": price,
            }
        )

    trades = pd.DataFrame(rows)
    if trades.empty:
        return trades
    _warn_missing_prices(trades)

    trades = _apply_turnover_budget(trades, nav, turnover)
    summary = _turnover_summary(trades, nav, turnover)
    trades = _prioritise(trades)
    trades = _add_priority_column(trades)

    if commit:
        # Approvals only: positions come from the broker after fills (execution/book_sync.py).
        _write_approvals(conn, trades, score_date)

    result = trades[
        [
            "ticker",
            "action",
            "current_shares",
            "target_shares",
            "delta_shares",
            "estimated_cost_usd",
            "priority",
            "direction",
            "price",
        ]
    ]
    result.attrs["turnover"] = summary
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def map_action(cur: float, tgt: float, delta: float) -> str:
    if abs(delta) < _HOLD_THRESHOLD:
        return "HOLD"
    if tgt > 0 and cur <= 0:
        return "BUY"
    if tgt >= 0 and cur > 0 and delta < 0:
        return "SELL"
    if tgt < 0 and cur >= 0:
        return "SHORT"
    if tgt <= 0 and cur < 0 and delta > 0:
        return "COVER"
    if tgt > 0 and cur > 0 and delta > 0:
        return "BUY"
    if tgt > 0 and cur > 0 and delta < 0:
        return "SELL"
    if tgt < 0 and cur < 0 and delta < 0:
        return "SHORT"
    return "HOLD"


def _last_close(price_df: pd.DataFrame) -> float:
    close_col = "close" if (not price_df.empty and "close" in price_df.columns) else "adj_close"
    if price_df.empty or close_col not in price_df.columns:
        return 0.0
    return float(price_df[close_col].iloc[-1])


def _planned_shares(
    tgt_map: dict[str, dict], price_of: dict[str, float], nav: float, config: dict
) -> dict[str, float]:
    """Whole-share targets within the position cap and, per sector, the sector gross cap."""
    cap = position_cap(config)
    # Alpaca limit orders are whole shares
    shares = {
        t: _cap_shares(round(info.get("shares", 0.0)), price_of[t], nav, cap)
        for t, info in tgt_map.items()
    }
    by_sector: dict[str, dict[str, float]] = {}
    for t, info in tgt_map.items():
        sector = info.get("sector")
        if isinstance(sector, str) and sector and price_of[t] > 0:
            by_sector.setdefault(sector, {})[t] = float(info.get("shares", 0.0))
    sector_cap = sector_gross_cap(config)
    for raw in by_sector.values():
        shares.update(_cap_sector_shares(shares, raw, price_of, nav, sector_cap))
    return shares


def _cap_shares(shares: float, price: float, nav: float, cap: float) -> float:
    """Whole shares worth at most cap x NAV at price, sign kept; unpriced targets unchanged.

    Optimiser renormalisation and whole-share rounding can land a target just above the cap,
    which the pre-trade POSITION_SIZE check would reject (packet 66).
    """
    if not price > 0 or nav <= 0 or abs(shares) * price / nav <= cap:  # `not >` catches NaN
        return shares
    limit = math.floor(cap * nav / price)
    if limit * price / nav > cap:  # float edge: the floor still prices a hair above the cap
        limit -= 1
    return math.copysign(limit, shares) + 0.0


def _cap_sector_shares(
    shares: dict[str, float],
    raw: dict[str, float],
    price_of: dict[str, float],
    nav: float,
    cap: float,
) -> dict[str, float]:
    """One sector's whole shares (tickers in raw) with combined |value| <= cap x NAV.

    The optimisers plan a sector up to the cap; rounding to whole shares can tip it over, which
    the pre-trade sector check would reject (packet 66). Truncate toward zero (scaled if the
    plan itself is over), then take shares off the largest position until within the cap.
    """
    out = {t: shares[t] for t in raw}
    if nav <= 0 or _value(out, price_of) <= cap * nav:
        return out
    raw_value = _value(raw, price_of)
    scale = min(1.0, cap * nav / raw_value) if raw_value > 0 else 0.0
    for t in out:
        whole = min(abs(out[t]), math.floor(abs(raw[t]) * scale))
        out[t] = math.copysign(whole, out[t]) + 0.0
    while _value(out, price_of) > cap * nav:  # float edge after flooring
        largest = max(out, key=lambda t: abs(out[t]) * price_of[t])
        out[largest] -= math.copysign(1.0, out[largest])
    return out


def _value(shares: dict[str, float], price_of: dict[str, float]) -> float:
    return sum(abs(s) * price_of[t] for t, s in shares.items())


def _warn_missing_prices(trades: pd.DataFrame) -> None:
    """Name every trade-list ticker valued at $0 for want of a price (packet 63)."""
    missing = sorted(trades.loc[~(trades["price"] > 0), "ticker"])
    if missing:
        logger.warning(
            "No price for %d trade-list ticker(s), valued at $0: %s",
            len(missing),
            ", ".join(missing),
        )


def _infer_direction(shares: float) -> str:
    return "LONG" if shares >= 0 else "SHORT"


def _apply_turnover_budget(trades: pd.DataFrame, nav: float, budget: float) -> pd.DataFrame:
    """Trim trades beyond the rotation budget; the move to each book's target gross is exempt."""
    df = trades.copy()
    df["trade_value"] = df["delta_shares"].abs() * df["price"].fillna(0)
    df["budget_value"] = _budget_values(df)
    df["trimmed"] = False
    proposed_turnover = df["budget_value"].sum() / nav if nav > 0 else 0.0

    if proposed_turnover <= budget:
        return df

    # Full closures are never trimmed (target_shares == 0)
    closures = df[df["target_shares"].abs() < _HOLD_THRESHOLD].copy()
    adjustable = df[df["target_shares"].abs() >= _HOLD_THRESHOLD].copy()

    budget_remaining = budget * nav - closures["budget_value"].sum()

    # Split budget proportionally between long and short books so that score
    # distribution asymmetry (sector_rank min > 0) cannot starve the short book.
    is_short = adjustable["direction"] == "SHORT"
    long_adj = adjustable[~is_short].copy()
    short_adj = adjustable[is_short].copy()

    long_tv = long_adj["budget_value"].sum()
    short_tv = short_adj["budget_value"].sum()
    total_tv = long_tv + short_tv

    if total_tv > 0:
        long_budget = budget_remaining * long_tv / total_tv
        short_budget = budget_remaining * short_tv / total_tv
    else:
        long_budget = short_budget = budget_remaining / 2

    long_keep, _ = _fill_book(long_adj, long_budget)
    short_keep, _ = _fill_book(short_adj, short_budget)

    keep_rows = set(long_keep + short_keep)
    allowed = adjustable.loc[adjustable.index.isin(keep_rows)]
    trimmed = adjustable.loc[~adjustable.index.isin(keep_rows)].copy()
    trimmed["trimmed"] = True
    trimmed["orig_action"] = trimmed["action"]
    trimmed["orig_delta"] = trimmed["delta_shares"]
    trimmed["orig_cost"] = trimmed["estimated_cost_usd"]
    trimmed["action"] = "HOLD"
    trimmed["delta_shares"] = 0.0
    trimmed["estimated_cost_usd"] = 0.0

    return pd.concat([closures, allowed, trimmed], ignore_index=True)


def _fill_book(book: pd.DataFrame, book_budget: float) -> tuple[list, list]:
    """Keep trades by conviction (|score - 50|, highest first), skipping any that do not fit."""
    book = book.copy()
    book["_conviction"] = (book["combined_score"].fillna(50) - 50).abs()
    book = book.sort_values("_conviction", ascending=False)  # highest first
    keep, trim = [], []
    used = 0.0
    for idx, row in book.iterrows():
        if used + row["budget_value"] <= book_budget:
            keep.append(idx)
            used += row["budget_value"]
        else:
            trim.append(idx)
    return keep, trim


def _budget_values(df: pd.DataFrame) -> pd.Series:
    """Trade value that spends the rotation budget (packet 57).

    Per book, trades that move it toward its target gross (opens/adds when the book is below
    target, reductions/closures when above) are exempt up to |target gross - current gross|.
    """
    counted = df["trade_value"].copy()
    price = df["price"].fillna(0)
    cur, tgt = df["current_shares"], df["target_shares"]
    conviction = (df["combined_score"].fillna(50) - 50).abs()
    is_closure = (tgt.abs() < _HOLD_THRESHOLD).astype(int)
    for sign, book in ((1, "LONG"), (-1, "SHORT")):
        gap = ((sign * tgt).clip(lower=0) * price).sum() - (
            (sign * cur).clip(lower=0) * price
        ).sum()
        moves_with_gap = tgt.abs() > cur.abs() if gap > 0 else tgt.abs() < cur.abs()
        eligible = df.index[(df["direction"] == book) & moves_with_gap]
        order = sorted(eligible, key=lambda i: (-is_closure[i], -conviction[i]))
        left = abs(gap)
        for idx in order:
            exempt = min(counted[idx], left)
            counted[idx] -= exempt
            left -= exempt
    return counted


def _turnover_summary(df: pd.DataFrame, nav: float, budget: float) -> dict:
    trimmed = df["trimmed"]
    return {
        "nav_usd": nav,
        "needed_usd": float(df["trade_value"].sum()),
        "allowance_usd": float((df["trade_value"] - df["budget_value"]).sum()),
        "rotation_budget_usd": budget * nav,
        "kept_usd": float(df.loc[~trimmed, "trade_value"].sum()),
        "kept_count": int((df["action"] != "HOLD").sum()),
        "trimmed_usd": float(df.loc[trimmed, "trade_value"].sum()),
        "trimmed_count": int(trimmed.sum()),
    }


def _prioritise(trades: pd.DataFrame) -> pd.DataFrame:
    """Order: closures first, then by abs(delta) descending."""
    df = trades.copy()
    df["_is_closure"] = df["target_shares"].abs() < _HOLD_THRESHOLD
    df["_abs_delta"] = df["delta_shares"].abs()
    df = df.sort_values(["_is_closure", "_abs_delta"], ascending=[False, False])
    return df.drop(columns=["_is_closure", "_abs_delta"])


def _add_priority_column(trades: pd.DataFrame) -> pd.DataFrame:
    df = trades.reset_index(drop=True)
    df["priority"] = df.index + 1
    return df


def _write_approvals(conn, trades: pd.DataFrame, score_date: str) -> None:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    rows = []
    for _, row in trades[(trades["action"] != "HOLD") | trades["trimmed"]].iterrows():
        trimmed = bool(row["trimmed"])  # kept for the record; nothing downstream reads TRIMMED
        rows.append(
            {
                "rebalance_date": score_date,
                "ticker": row["ticker"],
                "action": row["orig_action"] if trimmed else row["action"],
                "target_shares": row["target_shares"],
                "current_shares": row["current_shares"],
                "delta_shares": row["orig_delta"] if trimmed else row["delta_shares"],
                "estimated_cost_usd": row["orig_cost"] if trimmed else row["estimated_cost_usd"],
                "status": "TRIMMED" if trimmed else "PENDING",
                "created_at": now,
                "reviewed_at": None,
            }
        )
    if rows:
        conn.execute(position_approvals.insert(), rows)
        conn.commit()
    logger.info(
        "Rebalance: wrote %d approval rows (%d TRIMMED)",
        len(rows),
        int(trades["trimmed"].sum()),
    )
