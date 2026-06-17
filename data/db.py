"""Database engine, schema metadata, and helpers."""

import logging
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.engine.url import make_url

logger = logging.getLogger(__name__)

metadata = sa.MetaData()

sp500_universe = sa.Table(
    "sp500_universe", metadata,
    sa.Column("ticker",            sa.String, primary_key=True),
    sa.Column("company_name",      sa.String),
    sa.Column("gics_sector",       sa.String),
    sa.Column("gics_sub_industry", sa.String),
    sa.Column("updated_at",        sa.String),
)

benchmark_tickers = sa.Table(
    "benchmark_tickers", metadata,
    sa.Column("ticker",   sa.String, primary_key=True),
    sa.Column("category", sa.String),
)

daily_prices = sa.Table(
    "daily_prices", metadata,
    sa.Column("ticker",    sa.String,  nullable=False),
    sa.Column("date",      sa.String,  nullable=False),
    sa.Column("open",      sa.Float),
    sa.Column("high",      sa.Float),
    sa.Column("low",       sa.Float),
    sa.Column("close",     sa.Float),
    sa.Column("adj_close", sa.Float),
    sa.Column("volume",    sa.Integer),
    sa.PrimaryKeyConstraint("ticker", "date"),
)

fundamentals = sa.Table(
    "fundamentals", metadata,
    sa.Column("ticker",              sa.String, nullable=False),
    sa.Column("period_type",         sa.String, nullable=False),
    sa.Column("period_end",          sa.String, nullable=False),
    sa.Column("revenue",             sa.Float),
    sa.Column("gross_profit",        sa.Float),
    sa.Column("operating_income",    sa.Float),
    sa.Column("ebit",                sa.Float),
    sa.Column("net_income",          sa.Float),
    sa.Column("rd_expense",          sa.Float),
    sa.Column("total_assets",        sa.Float),
    sa.Column("total_liabilities",   sa.Float),
    sa.Column("total_equity",        sa.Float),
    sa.Column("cash",                sa.Float),
    sa.Column("total_debt",          sa.Float),
    sa.Column("current_assets",      sa.Float),
    sa.Column("current_liabilities", sa.Float),
    sa.Column("accounts_receivable", sa.Float),
    sa.Column("retained_earnings",   sa.Float),
    sa.Column("shares_outstanding",  sa.Float),
    sa.Column("dividends_paid",      sa.Float),
    sa.Column("cfo",                 sa.Float),
    sa.Column("capex",               sa.Float),
    sa.Column("fcf",                 sa.Float),
    sa.Column("buybacks",            sa.Float),
    sa.Column("roe",                 sa.Float),
    sa.Column("roa",                 sa.Float),
    sa.Column("gross_margin",        sa.Float),
    sa.Column("operating_margin",    sa.Float),
    sa.Column("net_margin",          sa.Float),
    sa.Column("revenue_growth_yoy",  sa.Float),
    sa.Column("revenue_growth_qoq",  sa.Float),
    sa.Column("earnings_growth_yoy", sa.Float),
    sa.Column("earnings_growth_qoq", sa.Float),
    sa.Column("debt_to_equity",      sa.Float),
    sa.Column("current_ratio",       sa.Float),
    sa.Column("ar_to_revenue",       sa.Float),
    sa.Column("cfo_to_ni",           sa.Float),
    sa.Column("accruals_ratio",      sa.Float),
    sa.Column("working_capital",     sa.Float),
    sa.Column("asset_turnover",      sa.Float),
    sa.Column("updated_at",          sa.String),
    sa.PrimaryKeyConstraint("ticker", "period_type", "period_end"),
)

sec_filings = sa.Table(
    "sec_filings", metadata,
    sa.Column("id",           sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("ticker",       sa.String,  nullable=False),
    sa.Column("form_type",    sa.String,  nullable=False),
    sa.Column("filed_date",   sa.String),
    sa.Column("accession_no", sa.String,  unique=True),
    sa.Column("filing_url",          sa.String),
    sa.Column("content_text",        sa.Text),
    sa.Column("fetched_at",          sa.String),
    sa.Column("transcript_checked",  sa.Boolean, default=False),
)

insider_transactions = sa.Table(
    "insider_transactions", metadata,
    sa.Column("id",               sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("ticker",           sa.String, nullable=False),
    sa.Column("insider_name",     sa.String),
    sa.Column("insider_title",    sa.String),
    sa.Column("transaction_type", sa.String),
    sa.Column("transaction_code", sa.String),
    sa.Column("shares",           sa.Float),
    sa.Column("price",            sa.Float),
    sa.Column("date",             sa.String),
    sa.Column("ownership_type",   sa.String),
    sa.Column("is_open_market",   sa.Integer, default=0),
    sa.Column("is_ceo_cfo",       sa.Integer, default=0),
    sa.Column("accession_no",     sa.String),
    sa.Column("fetched_at",       sa.String),
    sa.UniqueConstraint("ticker", "accession_no", "insider_name", "date", "shares"),
)

insider_cluster_flags = sa.Table(
    "insider_cluster_flags", metadata,
    sa.Column("ticker",        sa.String,  nullable=False),
    sa.Column("window_start",  sa.String,  nullable=False),
    sa.Column("window_end",    sa.String),
    sa.Column("insider_count", sa.Integer),
    sa.Column("total_shares",  sa.Float),
    sa.Column("flagged_at",    sa.String),
    sa.PrimaryKeyConstraint("ticker", "window_start"),
)

institutional_holdings = sa.Table(
    "institutional_holdings", metadata,
    sa.Column("id",           sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("fund_name",    sa.String,  nullable=False),
    sa.Column("ticker",       sa.String,  nullable=False),
    sa.Column("shares_held",  sa.Float),
    sa.Column("market_value", sa.Float),
    sa.Column("report_date",  sa.String,  nullable=False),
    sa.Column("fetched_at",   sa.String),
    sa.UniqueConstraint("fund_name", "ticker", "report_date"),
)

institutional_summary = sa.Table(
    "institutional_summary", metadata,
    sa.Column("ticker",           sa.String, nullable=False),
    sa.Column("report_date",      sa.String, nullable=False),
    sa.Column("funds_holding",    sa.Integer),
    sa.Column("net_share_change", sa.Float),
    sa.Column("new_positions",    sa.Integer),
    sa.PrimaryKeyConstraint("ticker", "report_date"),
)

short_interest = sa.Table(
    "short_interest", metadata,
    sa.Column("ticker",          sa.String, nullable=False),
    sa.Column("date",            sa.String, nullable=False),
    sa.Column("shares_short",    sa.Float),
    sa.Column("short_ratio",     sa.Float),
    sa.Column("short_pct_float", sa.Float),
    sa.Column("fetched_at",      sa.String),
    sa.PrimaryKeyConstraint("ticker", "date"),
)

analyst_estimates = sa.Table(
    "analyst_estimates", metadata,
    sa.Column("ticker",           sa.String, nullable=False),
    sa.Column("date",             sa.String, nullable=False),
    sa.Column("eps_estimate_fwd", sa.Float),
    sa.Column("price_target",     sa.Float),
    sa.Column("num_analysts",     sa.Integer),
    sa.Column("fetched_at",       sa.String),
    sa.PrimaryKeyConstraint("ticker", "date"),
)

earnings_calendar = sa.Table(
    "earnings_calendar", metadata,
    sa.Column("ticker",        sa.String, nullable=False),
    sa.Column("earnings_date", sa.String, nullable=False),
    sa.Column("eps_estimate",  sa.Float),
    sa.Column("fetched_at",    sa.String),
    sa.PrimaryKeyConstraint("ticker", "earnings_date"),
)

earnings_transcripts = sa.Table(
    "earnings_transcripts", metadata,
    sa.Column("id",                  sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("ticker",              sa.String,  nullable=False),
    sa.Column("earnings_date",       sa.String),
    sa.Column("quarter",             sa.String),
    sa.Column("year",                sa.Integer),
    sa.Column("content",             sa.Text),
    sa.Column("fetched_at",          sa.String),
    sa.Column("source_accession_no", sa.String),
    sa.UniqueConstraint("ticker", "earnings_date"),
)

# Standalone indexes — registered in metadata so create_all picks them up
sa.Index("idx_prices_ticker",  daily_prices.c.ticker)
sa.Index("idx_prices_date",    daily_prices.c.date)
sa.Index("idx_fund_ticker",    fundamentals.c.ticker)
sa.Index("idx_insider_ticker", insider_transactions.c.ticker)
sa.Index("idx_inst_ticker",    institutional_holdings.c.ticker)


def _configure_sqlite(dbapi_conn, _record) -> None:
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def get_engine(url: str) -> sa.engine.Engine:
    engine = sa.create_engine(url, future=True)
    if engine.dialect.name == "sqlite":
        db_file = make_url(url).database
        if db_file and db_file != ":memory:":
            Path(db_file).parent.mkdir(parents=True, exist_ok=True)
        event.listen(engine, "connect", _configure_sqlite)
    return engine


def initialise_schema(engine: sa.engine.Engine) -> None:
    metadata.create_all(engine, checkfirst=True)
    # Add columns introduced after initial table creation (idempotent).
    # AUTOCOMMIT is required for DDL in PostgreSQL.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        _add_column_if_missing(conn, "earnings_transcripts", "source_accession_no", "TEXT")
        _add_column_if_missing(conn, "sec_filings", "transcript_checked", "BOOLEAN DEFAULT FALSE")
    logger.debug("Schema initialised")


def _add_column_if_missing(conn, table: str, column: str, col_type: str) -> None:
    """ALTER TABLE … ADD COLUMN if it doesn't already exist (PostgreSQL + SQLite)."""
    dialect = conn.dialect.name
    if dialect == "postgresql":
        conn.execute(sa.text(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {col_type}"
        ))
    else:
        try:
            conn.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
        except Exception:
            pass  # SQLite raises if column already exists


def _dialect_insert(conn: sa.engine.Connection):
    """Return the dialect-specific insert function (supports on_conflict_do_*)."""
    if conn.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert


def insert_or_replace(conn: sa.engine.Connection, table: sa.Table) -> sa.sql.Insert:
    """INSERT … ON CONFLICT (pk) DO UPDATE — equivalent to SQLite OR REPLACE."""
    ins = _dialect_insert(conn)
    stmt = ins(table)
    pk_names = {c.name for c in table.primary_key}
    non_pk = [c.name for c in table.columns if c.name not in pk_names]
    return stmt.on_conflict_do_update(
        index_elements=list(pk_names),
        set_={c: stmt.excluded[c] for c in non_pk},
    )


def insert_or_ignore(conn: sa.engine.Connection, table: sa.Table) -> sa.sql.Insert:
    """INSERT … ON CONFLICT DO NOTHING — equivalent to SQLite OR IGNORE."""
    return _dialect_insert(conn)(table).on_conflict_do_nothing()
