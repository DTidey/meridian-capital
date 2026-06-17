"""Read and write portfolio positions."""

import logging
from datetime import UTC, datetime

import pandas as pd
import sqlalchemy as sa

from data.db import insert_or_replace
from portfolio.db import portfolio_history, portfolio_positions

logger = logging.getLogger(__name__)


def load_positions(conn: sa.engine.Connection) -> pd.DataFrame:
    """Return current open positions; empty DataFrame if none."""
    rows = conn.execute(sa.select(portfolio_positions)).fetchall()
    cols = [c.name for c in portfolio_positions.columns]
    return pd.DataFrame(rows, columns=cols)


def save_positions(
    conn: sa.engine.Connection,
    positions_df: pd.DataFrame,
    score_date: str,
    nav_usd: float,
) -> None:
    """Upsert positions into portfolio_positions and append to portfolio_history."""
    now = _now_iso()

    # Compute weight and market_value if not already present
    df = positions_df.copy()
    if "market_value" not in df.columns:
        df["market_value"] = df["shares"] * df["current_price"]
    if "weight" not in df.columns:
        df["weight"] = df["market_value"] / nav_usd
    if "unrealized_pnl" not in df.columns:
        df["unrealized_pnl"] = (df["current_price"] - df["entry_price"]) * df["shares"]

    df["updated_at"] = now

    stmt = insert_or_replace(conn, portfolio_positions)
    records = df[[c.name for c in portfolio_positions.columns if c.name in df.columns]].to_dict(
        orient="records"
    )
    if records:
        conn.execute(stmt, records)

    # History snapshot
    history_rows = []
    for _, row in df.iterrows():
        history_rows.append(
            {
                "snapshot_date": score_date,
                "ticker": row.get("ticker"),
                "direction": row.get("direction"),
                "shares": row.get("shares"),
                "price": row.get("current_price"),
                "market_value": row.get("market_value"),
                "weight": row.get("weight"),
                "unrealized_pnl": row.get("unrealized_pnl"),
                "sector": row.get("sector"),
                "combined_score": row.get("combined_score"),
                "recorded_at": now,
            }
        )
    if history_rows:
        conn.execute(portfolio_history.insert(), history_rows)

    conn.commit()
    logger.info("State: saved %d positions for %s", len(df), score_date)


def get_nav(config: dict) -> float:
    """Return NAV from config."""
    return float(config.get("portfolio", {}).get("nav_usd", 10_000_000))


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
