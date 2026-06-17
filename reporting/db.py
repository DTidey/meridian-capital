"""Layer 7 table definitions — registered on the shared data.db metadata."""

import sqlalchemy as sa

from data.db import metadata

pnl_attribution = sa.Table(
    "pnl_attribution", metadata,
    sa.Column("date",             sa.String, primary_key=True),
    sa.Column("portfolio_return", sa.Float),
    sa.Column("spy_return",       sa.Float),
    sa.Column("beta_pnl",         sa.Float),
    sa.Column("sector_pnl",       sa.Float),
    sa.Column("factor_pnl",       sa.Float),
    sa.Column("alpha_pnl",        sa.Float),
    sa.Column("net_beta",         sa.Float),
    sa.Column("computed_at",      sa.String),
)

portfolio_nav = sa.Table(
    "portfolio_nav", metadata,
    sa.Column("date",         sa.String, primary_key=True),
    sa.Column("nav",          sa.Float),
    sa.Column("spy_close",    sa.Float),
    sa.Column("drawdown_pct", sa.Float),
    sa.Column("computed_at",  sa.String),
)

position_trades = sa.Table(
    "position_trades", metadata,
    sa.Column("id",           sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("ticker",       sa.String,  nullable=False),
    sa.Column("direction",    sa.String),
    sa.Column("entry_date",   sa.String),
    sa.Column("exit_date",    sa.String),
    sa.Column("entry_price",  sa.Float),
    sa.Column("exit_price",   sa.Float),
    sa.Column("shares",       sa.Float),
    sa.Column("realized_pnl", sa.Float),
    sa.Column("holding_days", sa.Integer),
    sa.Column("sector",       sa.String),
    sa.Column("entry_score",  sa.Float),
    sa.Column("entry_vix",    sa.Float),
)

sa.Index("idx_position_trades_ticker", position_trades.c.ticker)
sa.Index("idx_position_trades_exit",   position_trades.c.exit_date)

lp_letters = sa.Table(
    "lp_letters", metadata,
    sa.Column("letter_date",   sa.String, primary_key=True),
    sa.Column("doc_id",        sa.String),
    sa.Column("content",       sa.Text),
    sa.Column("generated_at",  sa.String),
)

weekly_commentary = sa.Table(
    "weekly_commentary", metadata,
    sa.Column("week_start",    sa.String, primary_key=True),
    sa.Column("content",       sa.Text),
    sa.Column("generated_at",  sa.String),
)
