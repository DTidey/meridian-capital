"""FIFO round-trip matching from portfolio_history; Spearman predictive power."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pandas as pd
import scipy.stats
import sqlalchemy as sa

from analysis.db import combined_scores
from data.db import daily_prices, sp500_universe
from portfolio.db import portfolio_history
from reporting.db import position_trades

if TYPE_CHECKING:
    import sqlalchemy.engine

log = logging.getLogger(__name__)


def build_trades(engine: sqlalchemy.engine.Engine) -> pd.DataFrame:
    """Build FIFO round-trips from portfolio_history and upsert into position_trades.

    Algorithm:
    - Sort history by (ticker, snapshot_date).
    - Entry: first snapshot a ticker appears (or re-appears after a gap / direction flip).
    - Exit: snapshot just before ticker disappears or direction changes.
    - Shares decrease (partial exit): create a closed trade for the exited portion.

    Returns DataFrame of all closed trades (exit_date is not null).
    """
    with engine.connect() as conn:
        hist = conn.execute(
            sa.select(
                portfolio_history.c.snapshot_date,
                portfolio_history.c.ticker,
                portfolio_history.c.direction,
                portfolio_history.c.shares,
                portfolio_history.c.price,
                portfolio_history.c.sector,
            ).order_by(
                portfolio_history.c.ticker,
                portfolio_history.c.snapshot_date,
            )
        ).fetchall()

        if not hist:
            log.warning("portfolio_history is empty — no trades to build")
            return pd.DataFrame()

        score_rows = conn.execute(
            sa.select(
                combined_scores.c.ticker,
                combined_scores.c.score_date,
                combined_scores.c.combined_score,
            )
        ).fetchall()
        score_map = {(r[0], r[1]): r[2] for r in score_rows}

        vix_rows = conn.execute(
            sa.select(daily_prices.c.date, daily_prices.c.adj_close)
            .where(daily_prices.c.ticker == "^VIX")
            .order_by(daily_prices.c.date)
        ).fetchall()
        vix_map = {r[0]: r[1] for r in vix_rows}

        sector_rows = conn.execute(
            sa.select(sp500_universe.c.ticker, sp500_universe.c.gics_sector)
        ).fetchall()
        sector_map = {r[0]: r[1] for r in sector_rows}

        existing_trades = conn.execute(
            sa.select(
                position_trades.c.ticker,
                position_trades.c.entry_date,
                position_trades.c.shares,
            ).where(position_trades.c.exit_date.isnot(None))
        ).fetchall()
        existing_keys = {(r[0], r[1], r[2]) for r in existing_trades}

    df = pd.DataFrame(hist, columns=["date", "ticker", "direction", "shares", "price", "sector"])
    records = []

    for ticker, grp in df.groupby("ticker", sort=False):
        grp = grp.sort_values("date").reset_index(drop=True)
        _process_ticker(ticker, grp, records, score_map, vix_map, sector_map, existing_keys)

    new_records = [
        r
        for r in records
        if r["exit_date"] is not None
        and (r["ticker"], r["entry_date"], r["shares"]) not in existing_keys
    ]

    if new_records:
        with engine.begin() as conn:
            conn.execute(position_trades.insert(), new_records)
        log.info("position_trades: inserted %d new closed trades", len(new_records))

    return pd.DataFrame(records)


def _process_ticker(
    ticker: str,
    grp: pd.DataFrame,
    records: list,
    score_map: dict,
    vix_map: dict,
    sector_map: dict,
    existing_keys: set,
) -> None:
    lots: list[dict] = []  # FIFO queue of open lots: {shares, entry_date, entry_price}

    prev_direction = None
    for _, row in grp.iterrows():
        date = row["date"]
        direction = row["direction"]
        shares = float(row["shares"])
        price = float(row["price"]) if row["price"] else 0.0
        sector = sector_map.get(ticker, row.get("sector") or "Unknown")
        entry_score = score_map.get((ticker, date))
        entry_vix = _nearest_vix(vix_map, date)

        # Direction flip — close all existing lots
        if prev_direction and direction != prev_direction:
            for lot in lots:
                records.append(
                    _trade(
                        ticker,
                        direction=prev_direction,
                        entry_date=lot["entry_date"],
                        exit_date=date,
                        entry_price=lot["entry_price"],
                        exit_price=price,
                        shares=lot["shares"],
                        sector=sector,
                        entry_score=lot.get("entry_score"),
                        entry_vix=lot.get("entry_vix"),
                    )
                )
            lots = []

        if not lots:
            # New entry
            lots.append(
                {
                    "shares": shares,
                    "entry_date": date,
                    "entry_price": price,
                    "entry_score": entry_score,
                    "entry_vix": entry_vix,
                }
            )
            prev_direction = direction
            continue

        total_open = sum(lot["shares"] for lot in lots)

        if shares > total_open * 1.05:
            # Position grew — add a new lot for the additional shares
            lots.append(
                {
                    "shares": shares - total_open,
                    "entry_date": date,
                    "entry_price": price,
                    "entry_score": entry_score,
                    "entry_vix": entry_vix,
                }
            )
        elif shares < total_open * 0.95:
            # Position shrank — FIFO close oldest lots
            to_close = total_open - shares
            while to_close > 0 and lots:
                lot = lots[0]
                close_shares = min(lot["shares"], to_close)
                records.append(
                    _trade(
                        ticker,
                        direction=direction,
                        entry_date=lot["entry_date"],
                        exit_date=date,
                        entry_price=lot["entry_price"],
                        exit_price=price,
                        shares=close_shares,
                        sector=sector,
                        entry_score=lot.get("entry_score"),
                        entry_vix=lot.get("entry_vix"),
                    )
                )
                lot["shares"] -= close_shares
                to_close -= close_shares
                if lot["shares"] <= 0:
                    lots.pop(0)

        prev_direction = direction

    # At the end, any remaining open lots are still open (exit_date=None)
    for lot in lots:
        records.append(
            _trade(
                ticker,
                direction=prev_direction or "LONG",
                entry_date=lot["entry_date"],
                exit_date=None,
                entry_price=lot["entry_price"],
                exit_price=None,
                shares=lot["shares"],
                sector=sector_map.get(ticker, "Unknown"),
                entry_score=lot.get("entry_score"),
                entry_vix=lot.get("entry_vix"),
            )
        )


def _trade(
    ticker,
    direction,
    entry_date,
    exit_date,
    entry_price,
    exit_price,
    shares,
    sector,
    entry_score,
    entry_vix,
) -> dict:
    if exit_date and exit_price and entry_price:
        sign = 1.0 if direction == "LONG" else -1.0
        realized_pnl = sign * (exit_price - entry_price) * shares
        from datetime import date as _date

        try:
            hd = (_date.fromisoformat(exit_date) - _date.fromisoformat(entry_date)).days
        except Exception:
            hd = None
    else:
        realized_pnl = None
        hd = None

    return {
        "ticker": ticker,
        "direction": direction,
        "entry_date": entry_date,
        "exit_date": exit_date,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "shares": shares,
        "realized_pnl": realized_pnl,
        "holding_days": hd,
        "sector": sector,
        "entry_score": entry_score,
        "entry_vix": entry_vix,
    }


def _nearest_vix(vix_map: dict, date: str) -> float | None:
    if date in vix_map:
        return vix_map[date]
    candidates = sorted((d for d in vix_map if d <= date), reverse=True)
    return vix_map[candidates[0]] if candidates else None


def spearman_predictive_power(engine: sqlalchemy.engine.Engine) -> dict:
    """Spearman correlation between entry-time combined_score and realized return.

    Returns dict with keys: spearman_r, p_value, n — separately for LONG/SHORT.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(
                position_trades.c.direction,
                position_trades.c.entry_score,
                position_trades.c.realized_pnl,
                position_trades.c.entry_price,
                position_trades.c.shares,
            ).where(
                position_trades.c.exit_date.isnot(None),
                position_trades.c.entry_score.isnot(None),
                position_trades.c.realized_pnl.isnot(None),
                position_trades.c.entry_price > 0,
                position_trades.c.shares > 0,
            )
        ).fetchall()

    if not rows:
        return {"LONG": {}, "SHORT": {}}

    df = pd.DataFrame(
        rows, columns=["direction", "entry_score", "realized_pnl", "entry_price", "shares"]
    )
    df["return_pct"] = df["realized_pnl"] / (df["entry_price"] * df["shares"])

    result = {}
    for side in ("LONG", "SHORT"):
        sub = df[df["direction"] == side].dropna(subset=["entry_score", "return_pct"])
        if len(sub) < 3:
            result[side] = {"spearman_r": None, "p_value": None, "n": len(sub)}
            continue
        r, p = scipy.stats.spearmanr(sub["entry_score"], sub["return_pct"])
        result[side] = {
            "spearman_r": round(float(r), 4),
            "p_value": round(float(p), 4),
            "n": len(sub),
        }

    return result
