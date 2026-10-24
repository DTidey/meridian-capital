"""Trailing turnover analytics and FIFO-based tax estimate."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING

import pandas as pd
import sqlalchemy as sa

from execution.db import execution_orders
from reporting.db import portfolio_nav, position_trades

if TYPE_CHECKING:
    import sqlalchemy.engine

log = logging.getLogger(__name__)

_SHORT_TERM_RATE = 0.37
_LONG_TERM_RATE = 0.20
_LT_THRESHOLD_DAYS = 365


def compute(
    engine: sqlalchemy.engine.Engine,
    turnover_budget_pct: float = 0.30,
) -> dict:
    """Return turnover metrics and tax estimate.

    Returns dict with:
      turnover_30d_pct, turnover_90d_pct, turnover_annualized,
      budget_pct, tax_estimate_usd, short_term_gains, long_term_gains.
    """
    _today = date.today().isoformat()
    cutoff_30 = (date.today() - timedelta(days=30)).isoformat()
    cutoff_90 = (date.today() - timedelta(days=90)).isoformat()

    with engine.connect() as conn:
        nav_rows = conn.execute(
            sa.select(portfolio_nav.c.date, portfolio_nav.c.nav)
            .where(portfolio_nav.c.date >= cutoff_90)
            .order_by(portfolio_nav.c.date)
        ).fetchall()

        fill_rows = conn.execute(
            sa.select(
                execution_orders.c.rebalance_date,
                execution_orders.c.filled_shares,
                execution_orders.c.avg_fill_price,
            ).where(
                (execution_orders.c.rebalance_date >= cutoff_90)
                & (execution_orders.c.filled_shares > 0)
                & execution_orders.c.avg_fill_price.isnot(None)
            )
        ).fetchall()

        trade_rows = conn.execute(
            sa.select(
                position_trades.c.realized_pnl,
                position_trades.c.holding_days,
            ).where(
                position_trades.c.exit_date.isnot(None),
                position_trades.c.realized_pnl.isnot(None),
                position_trades.c.realized_pnl > 0,
            )
        ).fetchall()

    nav_df = pd.DataFrame(nav_rows, columns=["date", "nav"])
    fills_df = pd.DataFrame(fill_rows, columns=["date", "filled_shares", "avg_fill_price"])

    avg_nav_30 = _avg_nav(nav_df, cutoff_30)
    avg_nav_90 = _avg_nav(nav_df, cutoff_90)

    turnover_30 = _turnover_pct(fills_df, cutoff_30, avg_nav_30)
    turnover_90 = _turnover_pct(fills_df, cutoff_90, avg_nav_90)
    turnover_ann = turnover_30 * 12 if turnover_30 else 0.0

    # Tax estimate on all profitable closed trades
    st_gains, lt_gains = 0.0, 0.0
    for pnl, hd in trade_rows:
        if pnl <= 0:
            continue
        if hd and hd >= _LT_THRESHOLD_DAYS:
            lt_gains += pnl
        else:
            st_gains += pnl

    tax_estimate = st_gains * _SHORT_TERM_RATE + lt_gains * _LONG_TERM_RATE

    return {
        "turnover_30d_pct": round(turnover_30, 4),
        "turnover_90d_pct": round(turnover_90, 4),
        "turnover_annualized": round(turnover_ann, 4),
        "budget_pct": turnover_budget_pct,
        "tax_estimate_usd": round(tax_estimate, 2),
        "short_term_gains": round(st_gains, 2),
        "long_term_gains": round(lt_gains, 2),
    }


def _avg_nav(nav_df: pd.DataFrame, cutoff: str) -> float:
    sub = nav_df[nav_df["date"] >= cutoff]
    if sub.empty:
        return 0.0
    return float(sub["nav"].mean())


def _turnover_pct(fills_df: pd.DataFrame, cutoff: str, avg_nav: float) -> float:
    """Turnover = filled order value / 2 / avg_nav (fills of any final status count)."""
    if avg_nav <= 0:
        return 0.0
    sub = fills_df[fills_df["date"] >= cutoff]
    if sub.empty:
        return 0.0
    traded = (sub["filled_shares"].abs() * sub["avg_fill_price"]).sum()
    return float((traded / 2) / avg_nav)
