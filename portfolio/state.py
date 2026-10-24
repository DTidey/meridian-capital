"""Read portfolio positions; execution/book_sync.py writes them."""

import logging

import pandas as pd
import sqlalchemy as sa

from portfolio.db import portfolio_positions

logger = logging.getLogger(__name__)


def load_positions(conn: sa.engine.Connection) -> pd.DataFrame:
    """Return current open positions; empty DataFrame if none."""
    rows = conn.execute(sa.select(portfolio_positions)).fetchall()
    cols = [c.name for c in portfolio_positions.columns]
    return pd.DataFrame(rows, columns=cols)


def get_nav(config: dict) -> float:
    """Return NAV from config."""
    return float(config.get("portfolio", {}).get("nav_usd", 10_000_000))
