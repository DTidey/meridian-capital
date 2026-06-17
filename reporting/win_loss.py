"""Win/loss analytics from position_trades — no DB persistence."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pandas as pd
import sqlalchemy as sa

from reporting.db import position_trades

if TYPE_CHECKING:
    import sqlalchemy.engine

log = logging.getLogger(__name__)


def compute(engine: sqlalchemy.engine.Engine) -> dict:
    """Return win/loss stats sliced by side, holding period, sector, VIX regime, factor quintile.

    Only closed trades (exit_date IS NOT NULL) are counted.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(position_trades).where(
                position_trades.c.exit_date.isnot(None),
                position_trades.c.realized_pnl.isnot(None),
            )
        ).fetchall()

    if not rows:
        return _empty_result()

    df = pd.DataFrame(rows, columns=position_trades.columns.keys())
    df["win"] = df["realized_pnl"] > 0

    return {
        "overall": _stats(df),
        "by_side": {
            "LONG": _stats(df[df["direction"] == "LONG"]),
            "SHORT": _stats(df[df["direction"] == "SHORT"]),
        },
        "by_holding_period": _by_holding_period(df),
        "by_sector": _by_dimension(df, "sector"),
        "by_vix_regime": _by_vix_regime(df),
        "by_factor_quintile": _by_factor_quintile(df),
        "streaks": _streaks(df),
    }


# ---------------------------------------------------------------------------
# Slice helpers
# ---------------------------------------------------------------------------


def _stats(df: pd.DataFrame) -> dict:
    if df.empty:
        return {
            "win_rate": 0.0,
            "pl_ratio": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "total_trades": 0,
        }
    wins = df[df["win"]]
    losses = df[~df["win"]]
    avg_win = float(wins["realized_pnl"].mean()) if not wins.empty else 0.0
    avg_loss = float(losses["realized_pnl"].mean()) if not losses.empty else 0.0
    pl_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else 0.0
    return {
        "win_rate": round(len(wins) / len(df), 4),
        "pl_ratio": round(pl_ratio, 4),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "total_trades": len(df),
    }


def _by_holding_period(df: pd.DataFrame) -> dict:
    buckets = {
        "1-5d": df[df["holding_days"].between(1, 5)],
        "5-20d": df[df["holding_days"].between(5, 20)],
        "20-60d": df[df["holding_days"].between(20, 60)],
        "60d+": df[df["holding_days"] > 60],
    }
    return {k: _stats(v) for k, v in buckets.items()}


def _by_dimension(df: pd.DataFrame, col: str) -> dict:
    result = {}
    for val, grp in df.groupby(col):
        result[str(val)] = _stats(grp)
    return result


def _by_vix_regime(df: pd.DataFrame) -> dict:
    buckets = {
        "low (<15)": df[df["entry_vix"] < 15],
        "mid (15-25)": df[df["entry_vix"].between(15, 25)],
        "high (>25)": df[df["entry_vix"] > 25],
    }
    return {k: _stats(v) for k, v in buckets.items()}


def _by_factor_quintile(df: pd.DataFrame) -> dict:
    scored = df.dropna(subset=["entry_score"])
    if len(scored) < 5:
        return {}
    scored = scored.copy()
    try:
        scored["quintile"] = pd.qcut(
            scored["entry_score"], 5, labels=[1, 2, 3, 4, 5], duplicates="drop"
        )
    except ValueError:
        # Fewer than 5 unique bins after dedup — use rank-based assignment
        scored["quintile"] = pd.qcut(
            scored["entry_score"].rank(method="first"),
            5,
            labels=[1, 2, 3, 4, 5],
        )
    return {int(q): _stats(grp) for q, grp in scored.groupby("quintile")}


def _streaks(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"longest_win_streak": 0, "longest_loss_streak": 0, "current_streak": "none"}
    seq = df.sort_values("exit_date")["win"].tolist()
    longest_win, longest_loss, cur_win, cur_loss = 0, 0, 0, 0
    for w in seq:
        if w:
            cur_win += 1
            cur_loss = 0
        else:
            cur_loss += 1
            cur_win = 0
        longest_win = max(longest_win, cur_win)
        longest_loss = max(longest_loss, cur_loss)
    current = (
        f"+{cur_win} wins" if cur_win > 0 else (f"-{cur_loss} losses" if cur_loss > 0 else "none")
    )
    return {
        "longest_win_streak": longest_win,
        "longest_loss_streak": longest_loss,
        "current_streak": current,
    }


def _empty_result() -> dict:
    zero = {"win_rate": 0.0, "pl_ratio": 0.0, "avg_win": 0.0, "avg_loss": 0.0, "total_trades": 0}
    return {
        "overall": zero,
        "by_side": {"LONG": zero, "SHORT": zero},
        "by_holding_period": {},
        "by_sector": {},
        "by_vix_regime": {},
        "by_factor_quintile": {},
        "streaks": {"longest_win_streak": 0, "longest_loss_streak": 0, "current_streak": "none"},
    }
