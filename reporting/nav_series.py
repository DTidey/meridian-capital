"""Complete the daily NAV series in portfolio_nav with SPY closes and drawdown."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pandas as pd
import sqlalchemy as sa

from data.db import daily_prices
from reporting.db import portfolio_nav

if TYPE_CHECKING:
    import sqlalchemy.engine

log = logging.getLogger(__name__)


def build_nav_series(engine: sqlalchemy.engine.Engine) -> pd.DataFrame:
    """Fill spy_close and drawdown_pct for every recorded NAV; never change nav.

    NAV is Alpaca account equity, recorded each night by execution/book_sync.py.
    Drawdown = (running peak NAV - NAV) / running peak NAV.

    Returns DataFrame(date, nav, spy_close, drawdown_pct).
    """
    with engine.begin() as conn:
        rows = conn.execute(
            sa.select(portfolio_nav.c.date, portfolio_nav.c.nav)
            .where(portfolio_nav.c.nav.isnot(None))
            .order_by(portfolio_nav.c.date)
        ).fetchall()

        if not rows:
            log.warning("portfolio_nav is empty — no NAV recorded yet")
            return pd.DataFrame(columns=["date", "nav", "spy_close", "drawdown_pct"])

        df = pd.DataFrame(rows, columns=["date", "nav"])
        spy_rows = conn.execute(
            sa.select(daily_prices.c.date, daily_prices.c.adj_close)
            .where(daily_prices.c.ticker == "SPY")
            .where(daily_prices.c.date.in_(df["date"].tolist()))
        ).fetchall()
        df["spy_close"] = df["date"].map(dict(spy_rows))
        peak = df["nav"].cummax()
        df["drawdown_pct"] = ((peak - df["nav"]) / peak).where(peak > 0, 0.0)

        now = datetime.now(UTC).isoformat()
        for row in df.itertuples():
            conn.execute(
                portfolio_nav.update()
                .where(portfolio_nav.c.date == row.date)
                .values(
                    spy_close=None if pd.isna(row.spy_close) else float(row.spy_close),
                    drawdown_pct=float(row.drawdown_pct),
                    computed_at=now,
                )
            )

    log.info("NAV series: %d rows completed", len(df))
    return df[["date", "nav", "spy_close", "drawdown_pct"]]
