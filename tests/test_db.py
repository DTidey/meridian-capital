"""Schema creation and connection tests."""

import os
import re
from types import SimpleNamespace

import sqlalchemy as sa

from data.db import (
    _migrate_insider_unique_key,
    _widen_volume_column,
    daily_prices,
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
    assert tables >= EXPECTED_TABLES


def test_all_indexes_created(tmp_db):
    indexes = {
        r[0]
        for r in tmp_db.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='index'")
        ).fetchall()
    }
    assert indexes >= EXPECTED_INDEXES


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
    assert tables >= EXPECTED_TABLES


def test_get_engine_creates_parent_dirs(tmp_path):
    db_path = str(tmp_path / "nested" / "deep" / "test.db")
    engine = get_engine(f"sqlite:///{db_path}")
    initialise_schema(engine)  # first connection creates the file
    engine.dispose()
    assert os.path.exists(db_path)


def test_daily_prices_primary_key(tmp_db):
    """(ticker, date) is the PK — duplicate insert should replace, not error."""
    tmp_db.execute(sa.insert(daily_prices).values(ticker="AAPL", date="2024-01-01", close=100.0))
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
    """Duplicate (ticker, accession_no, txn_index) is silently ignored."""
    row = {
        "txn_index": 0,
        "ticker": "AAPL",
        "insider_name": "Alice",
        "insider_title": "CEO",
        "transaction_type": "Purchase",
        "transaction_code": "P",
        "shares": 1000,
        "price": 150.0,
        "date": "2024-01-01",
        "ownership_type": "D",
        "is_open_market": 1,
        "is_ceo_cfo": 1,
        "accession_no": "acc-001",
    }
    tmp_db.execute(sa.insert(insider_transactions).values(**row))
    tmp_db.execute(insert_or_ignore(tmp_db, insider_transactions).values(**row))
    tmp_db.commit()
    count = tmp_db.execute(sa.select(sa.func.count()).select_from(insider_transactions)).scalar()
    assert count == 1


class _FakePostgresConn:
    """Records SQL; information_schema reports `data_type` for daily_prices.volume."""

    def __init__(self, data_type):
        self.dialect = type("D", (), {"name": "postgresql"})()
        self.data_type = data_type
        self.sql: list[str] = []

    def execute(self, stmt):
        self.sql.append(str(stmt))
        return type("R", (), {"scalar": lambda _self: self.data_type})()


def test_volume_migration_alters_integer_column():
    conn = _FakePostgresConn("integer")
    _widen_volume_column(conn)
    assert any("ALTER TABLE daily_prices ALTER COLUMN volume TYPE BIGINT" in s for s in conn.sql)


def test_volume_migration_noop_when_bigint():
    conn = _FakePostgresConn("bigint")
    _widen_volume_column(conn)
    assert not any("ALTER" in s for s in conn.sql)


def test_volume_migration_noop_on_sqlite(tmp_db):
    _widen_volume_column(tmp_db)  # no information_schema on SQLite: must not raise
    tmp_db.execute(sa.insert(daily_prices).values(ticker="A", date="2024-01-01", volume=2**40))
    assert tmp_db.execute(sa.select(daily_prices.c.volume)).scalar() == 2**40


# ---------------------------------------------------------------------------
# AC7 (packet 39): insider unique key (ticker, accession_no, txn_index)
# ---------------------------------------------------------------------------

NEW_KEY = ["ticker", "accession_no", "txn_index"]
OLD_KEY = ["ticker", "accession_no", "insider_name", "date", "shares"]
# Postgres's own name for the old key: identifiers are cut to 63 characters, so the column
# part loses "shares" and keeps its trailing underscore.
OLD_PG_NAME = "insider_transactions_ticker_accession_no_insider_name_date__key"


def _insider_row(**kw):
    row = {
        "ticker": "TT",
        "insider_name": "Alice",
        "transaction_code": "M",
        "shares": 43778.0,
        "date": "2026-10-01",
        "accession_no": "acc-1",
        "txn_index": 0,
    }
    row.update(kw)
    return row


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _FakePgConn:
    """Enough of a PostgreSQL catalog to exercise the migration's DDL path."""

    dialect = SimpleNamespace(name="postgresql")

    def __init__(self, constraints: dict[str, list[str]], columns: set[str]):
        self.constraints = dict(constraints)
        self.columns = set(columns)
        self.ddl: list[str] = []

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        if sql.startswith("ALTER TABLE"):
            return self._alter(sql)
        if "conkey" in sql:
            want = sorted(params["cols"])
            return _Result([n for n, c in self.constraints.items() if sorted(c) == want])
        if "conname" in sql:
            return _Result([1] if params["name"] in self.constraints else [])
        raise AssertionError(f"unexpected SQL: {sql}")

    def _alter(self, sql):
        if "ADD COLUMN IF NOT EXISTS" in sql:
            col = sql.split("IF NOT EXISTS ")[1].split()[0]
            if col in self.columns:
                return _Result([])
            self.columns.add(col)
        elif "DROP CONSTRAINT" in sql:
            del self.constraints[re.search(r'DROP CONSTRAINT "([^"]+)"', sql).group(1)]
        elif "ADD CONSTRAINT" in sql:
            m = re.search(r"ADD CONSTRAINT (\S+) UNIQUE \(([^)]+)\)", sql)
            self.constraints[m.group(1)] = [c.strip() for c in m.group(2).split(",")]
        self.ddl.append(sql)
        return _Result([])


class TestInsiderUniqueMigration:
    def _unique_keys(self, engine):
        return [
            uc["column_names"]
            for uc in sa.inspect(engine).get_unique_constraints("insider_transactions")
        ]

    def test_fresh_schema_has_new_key(self, tmp_engine):
        assert self._unique_keys(tmp_engine) == [NEW_KEY]

    def test_same_shares_different_index_both_stored(self, tmp_db):
        tmp_db.execute(insert_or_ignore(tmp_db, insider_transactions).values(**_insider_row()))
        tmp_db.execute(
            insert_or_ignore(tmp_db, insider_transactions).values(
                **_insider_row(txn_index=1, transaction_code="S")
            )
        )
        tmp_db.commit()
        count = tmp_db.execute(
            sa.select(sa.func.count()).select_from(insider_transactions)
        ).scalar()
        assert count == 2

    def test_rerun_succeeds_and_changes_nothing(self, tmp_engine):
        with tmp_engine.begin() as conn:
            conn.execute(sa.insert(insider_transactions).values(**_insider_row()))
        initialise_schema(tmp_engine)
        initialise_schema(tmp_engine)
        assert self._unique_keys(tmp_engine) == [NEW_KEY]
        with tmp_engine.connect() as conn:
            rows = conn.execute(sa.select(insider_transactions.c.txn_index)).fetchall()
        assert rows == [(0,)]

    def test_pg_replaces_old_key(self):
        conn = _FakePgConn({OLD_PG_NAME: OLD_KEY}, {"ticker", "accession_no"})
        _migrate_insider_unique_key(conn)
        assert conn.constraints == {
            "insider_transactions_ticker_accession_no_txn_index_key": NEW_KEY
        }
        assert "txn_index" in conn.columns
        assert any(f'DROP CONSTRAINT "{OLD_PG_NAME}"' in d for d in conn.ddl)

    def test_pg_second_run_changes_nothing(self):
        conn = _FakePgConn({OLD_PG_NAME: OLD_KEY}, {"ticker", "accession_no"})
        _migrate_insider_unique_key(conn)
        after_first = dict(conn.constraints)
        conn.ddl.clear()
        _migrate_insider_unique_key(conn)
        assert conn.constraints == after_first
        assert not any("CONSTRAINT" in d for d in conn.ddl)

    # Adversarial: the old key under a different (e.g. hand-made) name is still found by columns.
    def test_pg_old_key_found_by_columns_not_name(self):
        conn = _FakePgConn({"legacy_uq": list(reversed(OLD_KEY))}, {"ticker"})
        _migrate_insider_unique_key(conn)
        assert "legacy_uq" not in conn.constraints

    # Adversarial: unrelated unique keys on the table are left alone.
    def test_pg_other_keys_untouched(self):
        conn = _FakePgConn({"other_uq": ["ticker", "date"]}, {"ticker"})
        _migrate_insider_unique_key(conn)
        assert conn.constraints["other_uq"] == ["ticker", "date"]

    def test_sqlite_path_issues_no_constraint_ddl(self):
        executed = []
        conn = SimpleNamespace(
            dialect=SimpleNamespace(name="sqlite"),
            execute=lambda stmt, params=None: executed.append(str(stmt)),
        )
        _migrate_insider_unique_key(conn)
        assert executed == ["ALTER TABLE insider_transactions ADD COLUMN txn_index INTEGER"]
