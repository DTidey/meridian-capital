"""Layer 6 table definitions — registered on the shared data.db metadata."""

import sqlalchemy as sa

from data.db import metadata

execution_orders = sa.Table(
    "execution_orders", metadata,
    sa.Column("id",              sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("rebalance_date",  sa.String,  nullable=False),
    sa.Column("ticker",          sa.String,  nullable=False),
    sa.Column("action",          sa.String,  nullable=False),   # BUY / SELL / SHORT / COVER
    sa.Column("ordered_shares",  sa.Float),
    sa.Column("filled_shares",   sa.Float,   default=0.0),
    sa.Column("avg_fill_price",  sa.Float),                     # null until filled
    sa.Column("order_id",        sa.String),                    # Alpaca UUID
    sa.Column("status",          sa.String,  default="PENDING"),# PENDING/PARTIAL/FILLED/CANCELLED/FAILED
    sa.Column("slippage_bps",    sa.Float),
    sa.Column("created_at",      sa.String),
    sa.Column("updated_at",      sa.String),
)

sa.Index("idx_exec_orders_date_ticker", execution_orders.c.rebalance_date, execution_orders.c.ticker)
sa.Index("idx_exec_orders_status",      execution_orders.c.status)
