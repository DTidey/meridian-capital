"""Read portfolio positions; execution/book_sync.py writes them."""

import logging

import pandas as pd
import sqlalchemy as sa

from portfolio.db import portfolio_positions
from reporting.db import portfolio_nav

logger = logging.getLogger(__name__)


def load_positions(conn: sa.engine.Connection) -> pd.DataFrame:
    """Return current open positions; empty DataFrame if none."""
    rows = conn.execute(sa.select(portfolio_positions)).fetchall()
    cols = [c.name for c in portfolio_positions.columns]
    return pd.DataFrame(rows, columns=cols)


def get_nav(config: dict, conn=None, score_date: str | None = None) -> float:
    """Latest recorded NAV (Alpaca equity) on or before score_date; config nav_usd if none."""
    fallback = float(config.get("portfolio", {}).get("nav_usd", 10_000_000))
    if conn is None:
        return fallback
    query = sa.select(portfolio_nav.c.nav).where(portfolio_nav.c.nav > 0)
    if score_date:
        query = query.where(portfolio_nav.c.date <= score_date)
    nav = conn.execute(query.order_by(portfolio_nav.c.date.desc()).limit(1)).scalar()
    return float(nav) if nav else fallback


def with_nav(config: dict, nav: float) -> dict:
    """Copy of config with portfolio.nav_usd set, for code that reads NAV from config."""
    return {**config, "portfolio": {**config.get("portfolio", {}), "nav_usd": nav}}
