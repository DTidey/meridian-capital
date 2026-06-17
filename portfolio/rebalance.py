"""Diff current vs target to generate a trade list."""

import logging
from datetime import UTC, datetime

import pandas as pd
import sqlalchemy as sa

from portfolio.db import portfolio_positions, position_approvals
from portfolio.state import save_positions
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

    cur_map = (
        current.set_index("ticker")["shares"].to_dict()
        if not current.empty and "shares" in current.columns
        else {}
    )
    tgt_map = (
        target.set_index("ticker")[["shares", "direction", "combined_score"]].to_dict(
            orient="index"
        )
        if not target.empty
        else {}
    )

    all_tickers = set(cur_map) | set(tgt_map)
    rows = []
    for ticker in all_tickers:
        cur_shares = cur_map.get(ticker, 0.0)
        tgt_info = tgt_map.get(ticker, {})
        tgt_shares = tgt_info.get("shares", 0.0)
        direction = tgt_info.get("direction") or _infer_direction(cur_shares)
        delta = tgt_shares - cur_shares
        action = _map_action(cur_shares, tgt_shares, delta)

        price_df = prices.get(ticker, pd.DataFrame())
        close_col = "close" if (not price_df.empty and "close" in price_df.columns) else "adj_close"
        price = (
            float(price_df[close_col].iloc[-1])
            if (not price_df.empty and close_col in price_df.columns)
            else 0.0
        )
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

    trades = _apply_turnover_budget(trades, nav, turnover)
    trades = _prioritise(trades)
    trades = _add_priority_column(trades)

    if commit:
        _write_approvals(conn, trades, score_date)
        _update_positions(conn, trades, target, score_date, nav)

    return trades[
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _map_action(cur: float, tgt: float, delta: float) -> str:
    if abs(delta) < _HOLD_THRESHOLD:
        return "HOLD"
    if tgt > 0 and cur <= 0:
        return "BUY"
    if tgt >= 0 and cur > 0 and delta < 0:
        return "SELL"
    if tgt < 0 and cur >= 0:
        return "SHORT"
    if tgt < 0 and cur < 0 and delta > 0:
        return "COVER"
    if tgt > 0 and cur > 0 and delta > 0:
        return "BUY"
    if tgt > 0 and cur > 0 and delta < 0:
        return "SELL"
    if tgt < 0 and cur < 0 and delta < 0:
        return "SHORT"
    return "HOLD"


def _infer_direction(shares: float) -> str:
    return "LONG" if shares >= 0 else "SHORT"


def _apply_turnover_budget(trades: pd.DataFrame, nav: float, budget: float) -> pd.DataFrame:
    df = trades.copy()
    df["trade_value"] = df["delta_shares"].abs() * df["price"].fillna(0)
    proposed_turnover = df["trade_value"].sum() / nav if nav > 0 else 0.0

    if proposed_turnover <= budget:
        return df

    # Full closures are never trimmed (target_shares == 0)
    closures = df[df["target_shares"].abs() < _HOLD_THRESHOLD].copy()
    adjustable = df[df["target_shares"].abs() >= _HOLD_THRESHOLD].copy()

    closure_tv = closures["trade_value"].sum()
    budget_remaining = budget * nav - closure_tv

    # Split budget proportionally between long and short books so that score
    # distribution asymmetry (sector_rank min > 0) cannot starve the short book.
    is_short = adjustable["direction"] == "SHORT"
    long_adj = adjustable[~is_short].copy()
    short_adj = adjustable[is_short].copy()

    long_tv = long_adj["trade_value"].sum()
    short_tv = short_adj["trade_value"].sum()
    total_tv = long_tv + short_tv

    if total_tv > 0:
        long_budget = budget_remaining * long_tv / total_tv
        short_budget = budget_remaining * short_tv / total_tv
    else:
        long_budget = short_budget = budget_remaining / 2

    def _fill_book(book: pd.DataFrame, book_budget: float) -> tuple[list, list]:
        book = book.copy()
        book["_conviction"] = (book["combined_score"].fillna(50) - 50).abs()
        book = book.sort_values("_conviction", ascending=False)  # highest first
        keep, trim = [], []
        used = 0.0
        for idx, row in book.iterrows():
            if used + row["trade_value"] <= book_budget:
                keep.append(idx)
                used += row["trade_value"]
            else:
                trim.append(idx)
        return keep, trim

    long_keep, long_trim = _fill_book(long_adj, long_budget)
    short_keep, short_trim = _fill_book(short_adj, short_budget)

    keep_rows = set(long_keep + short_keep)
    allowed = adjustable.loc[adjustable.index.isin(keep_rows)]
    trimmed = adjustable.loc[~adjustable.index.isin(keep_rows)].copy()
    trimmed["action"] = "HOLD"
    trimmed["delta_shares"] = 0.0
    trimmed["estimated_cost_usd"] = 0.0

    return pd.concat([closures, allowed, trimmed], ignore_index=True)


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
    for _, row in trades[trades["action"] != "HOLD"].iterrows():
        rows.append(
            {
                "rebalance_date": score_date,
                "ticker": row["ticker"],
                "action": row["action"],
                "target_shares": row["target_shares"],
                "current_shares": row["current_shares"],
                "delta_shares": row["delta_shares"],
                "estimated_cost_usd": row["estimated_cost_usd"],
                "status": "PENDING",
                "created_at": now,
                "reviewed_at": None,
            }
        )
    if rows:
        conn.execute(position_approvals.insert(), rows)
        conn.commit()
    logger.info("Rebalance: wrote %d approval rows", len(rows))


def _update_positions(
    conn,
    trades: pd.DataFrame,
    target: pd.DataFrame,
    score_date: str,
    nav: float,
) -> None:
    """Update portfolio_positions only for trades that actually executed (not HOLD).

    - Full closures (target_shares ≈ 0): delete the row from portfolio_positions.
    - Partial adjustments / new positions: upsert from target.
    """
    active = trades[trades["action"] != "HOLD"]
    if active.empty:
        return

    closed_tickers = set(active.loc[active["target_shares"].abs() < _HOLD_THRESHOLD, "ticker"])
    adjusted_tickers = set(active["ticker"]) - closed_tickers

    if closed_tickers:
        conn.execute(
            portfolio_positions.delete().where(
                portfolio_positions.c.ticker.in_(list(closed_tickers))
            )
        )
        conn.commit()
        logger.debug("Positions: deleted %d closed positions", len(closed_tickers))

    if adjusted_tickers and not target.empty:
        tgt = target[target["ticker"].isin(adjusted_tickers)].copy()
        if not tgt.empty:
            if "entry_price" not in tgt.columns:
                tgt["entry_price"] = tgt.get("current_price", 0.0)
            if "entry_date" not in tgt.columns:
                tgt["entry_date"] = score_date
            save_positions(conn, tgt, score_date, nav)
            logger.info("Positions: committed %d new/adjusted positions", len(tgt))
