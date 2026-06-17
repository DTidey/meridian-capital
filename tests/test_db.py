"""Schema creation and connection tests."""

import os

import sqlalchemy as sa

from data.db import (
    analyst_estimates,
    daily_prices,
    fundamentals,
    get_engine,
    initialise_schema,
    insert_or_ignore,
    insert_or_replace,
    insider_transactions,
)

EXPECTED_TABLES = {
    "sp500_universe",
    "benchmark_tickers",
    "daily_prices",
    "fundamentals",
    "sec_filings",
    "insider_transactions",
    "insider_cluster_flags",
    "institutional_holdings",
    "institutional_summary",
    "short_interest",
    "analyst_estimates",
    "earnings_calendar",
    "earnings_transcripts",
}

EXPECTED_INDEXES = {
    "idx_prices_ticker",
    "idx_prices_date",
    "idx_fund_ticker",
    "idx_insider_ticker",
    "idx_inst_ticker",
}


def test_all_tables_created(tmp_db):
    tables = {
        r[0]
        for r in tmp_db.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='table'")
        ).fetchall()
    }
    assert EXPECTED_TABLES <= tables


def test_all_indexes_created(tmp_db):
    indexes = {
        r[0]
        for r in tmp_db.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='index'")
        ).fetchall()
    }
    assert EXPECTED_INDEXES <= indexes


def test_wal_mode_enabled(tmp_db):
    mode = tmp_db.execute(sa.text("PRAGMA journal_mode")).fetchone()[0]
    assert mode == "wal"


def test_foreign_keys_enabled(tmp_db):
    fk = tmp_db.execute(sa.text("PRAGMA foreign_keys")).fetchone()[0]
    assert fk == 1


def test_schema_is_idempotent(tmp_engine):
    """Running initialise_schema twice must not raise."""
    initialise_schema(tmp_engine)
    initialise_schema(tmp_engine)
    with tmp_engine.connect() as conn:
        tables = {
            r[0]
            for r in conn.execute(
                sa.text("SELECT name FROM sqlite_master WHERE type='table'")
            ).fetchall()
        }
    assert EXPECTED_TABLES <= tables


def test_get_engine_creates_parent_dirs(tmp_path):
    db_path = str(tmp_path / "nested" / "deep" / "test.db")
    engine = get_engine(f"sqlite:///{db_path}")
    initialise_schema(engine)   # first connection creates the file
    engine.dispose()
    assert os.path.exists(db_path)


def test_daily_prices_primary_key(tmp_db):
    """(ticker, date) is the PK — duplicate insert should replace, not error."""
    tmp_db.execute(
        sa.insert(daily_prices).values(ticker="AAPL", date="2024-01-01", close=100.0)
    )
    tmp_db.execute(
        insert_or_replace(tmp_db, daily_prices).values(
            ticker="AAPL", date="2024-01-01", close=200.0
        )
    )
    tmp_db.commit()
    row = tmp_db.execute(
        sa.select(daily_prices.c.close).where(
            (daily_prices.c.ticker == "AAPL") & (daily_prices.c.date == "2024-01-01")
        )
    ).fetchone()
    assert row[0] == 200.0


def test_insider_transactions_unique_constraint(tmp_db):
    """Duplicate (ticker, accession_no, insider_name, date, shares) is silently ignored."""
    row = {
        "ticker": "AAPL", "insider_name": "Alice", "insider_title": "CEO",
        "transaction_type": "Purchase", "transaction_code": "P",
        "shares": 1000, "price": 150.0, "date": "2024-01-01",
        "ownership_type": "D", "is_open_market": 1, "is_ceo_cfo": 1,
        "accession_no": "acc-001",
    }
    tmp_db.execute(sa.insert(insider_transactions).values(**row))
    tmp_db.execute(insert_or_ignore(tmp_db, insider_transactions).values(**row))
    tmp_db.commit()
    count = tmp_db.execute(
        sa.select(sa.func.count()).select_from(insider_transactions)
    ).scalar()
    assert count == 1
