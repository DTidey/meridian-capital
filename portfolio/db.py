"""Layer 4 table definitions — registered on the shared data.db metadata."""

import sqlalchemy as sa

from data.db import metadata

portfolio_positions = sa.Table(
    "portfolio_positions", metadata,
    sa.Column("ticker",          sa.String,  nullable=False),
    sa.Column("direction",       sa.String),
    sa.Column("shares",          sa.Float),
    sa.Column("entry_price",     sa.Float),
    sa.Column("entry_date",      sa.String),
    sa.Column("current_price",   sa.Float),
    sa.Column("market_value",    sa.Float),
    sa.Column("weight",          sa.Float),
    sa.Column("unrealized_pnl",  sa.Float),
    sa.Column("sector",          sa.String),
    sa.Column("combined_score",  sa.Float),
    sa.Column("beta",            sa.Float),
    sa.Column("updated_at",      sa.String),
    sa.PrimaryKeyConstraint("ticker"),
)

portfolio_history = sa.Table(
    "portfolio_history", metadata,
    sa.Column("id",             sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("snapshot_date",  sa.String,  nullable=False),
    sa.Column("ticker",         sa.String,  nullable=False),
    sa.Column("direction",      sa.String),
    sa.Column("shares",         sa.Float),
    sa.Column("price",          sa.Float),
    sa.Column("market_value",   sa.Float),
    sa.Column("weight",         sa.Float),
    sa.Column("unrealized_pnl", sa.Float),
    sa.Column("sector",         sa.String),
    sa.Column("combined_score", sa.Float),
    sa.Column("recorded_at",    sa.String),
)

sa.Index("idx_port_history_date_ticker",
         portfolio_history.c.snapshot_date, portfolio_history.c.ticker)

position_approvals = sa.Table(
    "position_approvals", metadata,
    sa.Column("id",               sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("rebalance_date",   sa.String,  nullable=False),
    sa.Column("ticker",           sa.String,  nullable=False),
    sa.Column("action",           sa.String),
    sa.Column("target_shares",    sa.Float),
    sa.Column("current_shares",   sa.Float),
    sa.Column("delta_shares",     sa.Float),
    sa.Column("estimated_cost_usd", sa.Float),
    sa.Column("status",           sa.String,  default="PENDING"),
    sa.Column("created_at",       sa.String),
    sa.Column("reviewed_at",      sa.String),
)
