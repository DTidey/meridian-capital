"""Build daily NAV series from portfolio_history and persist to portfolio_nav."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pandas as pd
import sqlalchemy as sa

from data.db import daily_prices, insert_or_replace
from portfolio.db import portfolio_history
from reporting.db import portfolio_nav

if TYPE_CHECKING:
    import sqlalchemy.engine

log = logging.getLogger(__name__)

_NAV_BASE = 10_000_000.0  # fallback if config not passed


def build_nav_series(
    engine: sqlalchemy.engine.Engine,
    nav_usd: float = _NAV_BASE,
) -> pd.DataFrame:
    """Compute daily NAV from portfolio_history and upsert into portfolio_nav.

    NAV per day = nav_usd + sum(unrealized_pnl) across all positions that day.
    Drawdown = (peak_nav − nav) / peak_nav, rolling max from first date.

    Returns DataFrame(date, nav, spy_close, drawdown_pct).
    """
    with engine.begin() as conn:
        rows = conn.execute(
            sa.select(
                portfolio_history.c.snapshot_date,
                sa.func.sum(portfolio_history.c.unrealized_pnl).label("total_pnl"),
            )
            .group_by(portfolio_history.c.snapshot_date)
            .order_by(portfolio_history.c.snapshot_date)
        ).fetchall()

        if not rows:
            log.warning("portfolio_history is empty — no NAV series to build")
            return pd.DataFrame(columns=["date", "nav", "spy_close", "drawdown_pct"])

        dates = [r[0] for r in rows]
        nav_series = [nav_usd + r[1] for r in rows]

        spy_rows = conn.execute(
            sa.select(daily_prices.c.date, daily_prices.c.adj_close)
            .where(daily_prices.c.ticker == "SPY")
            .where(daily_prices.c.date.in_(dates))
            .order_by(daily_prices.c.date)
        ).fetchall()
        spy_map = {r[0]: r[1] for r in spy_rows}

        df = pd.DataFrame({"date": dates, "nav": nav_series})
        df["spy_close"] = df["date"].map(spy_map)
        df["peak_nav"] = df["nav"].cummax()
        df["drawdown_pct"] = (df["peak_nav"] - df["nav"]) / df["peak_nav"]

        now = datetime.now(UTC).isoformat()
        ins = insert_or_replace(conn, portfolio_nav)
        conn.execute(
            ins,
            [
                {
                    "date": row.date,
                    "nav": float(row.nav),
                    "spy_close": float(row.spy_close)
                    if row.spy_close is not None and row.spy_close == row.spy_close
                    else None,
                    "drawdown_pct": float(row.drawdown_pct),
                    "computed_at": now,
                }
                for row in df.itertuples()
            ],
        )

    log.info("NAV series: %d rows written", len(df))
    return df[["date", "nav", "spy_close", "drawdown_pct"]]
