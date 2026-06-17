"""Tests for reporting/nav_series.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import sqlalchemy as sa

import analysis.db  # noqa: F401
import execution.db  # noqa: F401
import factors.db  # noqa: F401
import portfolio.db  # noqa: F401
import reporting.db  # noqa: F401 — register tables
import risk.db  # noqa: F401
from data.db import daily_prices, initialise_schema
from portfolio.db import portfolio_history
from reporting.db import portfolio_nav
from reporting.nav_series import build_nav_series

_NAV_BASE = 10_000_000.0


@pytest.fixture
def engine(tmp_path):
    from data.db import get_engine

    eng = get_engine(f"sqlite:///{tmp_path / 'test.db'}")
    initialise_schema(eng)
    yield eng
    eng.dispose()


def _insert_history(conn, rows):
    conn.execute(portfolio_history.insert(), rows)


def _insert_spy(conn, rows):
    conn.execute(daily_prices.insert(), rows)


def test_build_nav_series_empty(engine):
    """Empty portfolio_history → empty DataFrame, no crash."""
    df = build_nav_series(engine, nav_usd=_NAV_BASE)
    assert df.empty


def test_build_nav_series_known_values(engine):
    """Three snapshot dates → NAV = nav_usd + sum(unrealized_pnl) per day."""
    hist_rows = [
        {
            "snapshot_date": "2026-01-01",
            "ticker": "AAPL",
            "direction": "LONG",
            "shares": 100.0,
            "price": 100.0,
            "market_value": 10000.0,
            "weight": 0.10,
            "unrealized_pnl": 500.0,
            "sector": "Technology",
            "combined_score": 80.0,
            "recorded_at": "2026-01-01T00:00:00",
        },
        {
            "snapshot_date": "2026-01-02",
            "ticker": "AAPL",
            "direction": "LONG",
            "shares": 100.0,
            "price": 101.0,
            "market_value": 10100.0,
            "weight": 0.10,
            "unrealized_pnl": 600.0,
            "sector": "Technology",
            "combined_score": 80.0,
            "recorded_at": "2026-01-02T00:00:00",
        },
        {
            "snapshot_date": "2026-01-03",
            "ticker": "AAPL",
            "direction": "LONG",
            "shares": 100.0,
            "price": 99.0,
            "market_value": 9900.0,
            "weight": 0.10,
            "unrealized_pnl": 400.0,
            "sector": "Technology",
            "combined_score": 80.0,
            "recorded_at": "2026-01-03T00:00:00",
        },
    ]
    with engine.begin() as conn:
        _insert_history(conn, hist_rows)

    df = build_nav_series(engine, nav_usd=_NAV_BASE)

    assert len(df) == 3
    assert abs(df.loc[df["date"] == "2026-01-01", "nav"].iloc[0] - (_NAV_BASE + 500.0)) < 0.01
    assert abs(df.loc[df["date"] == "2026-01-02", "nav"].iloc[0] - (_NAV_BASE + 600.0)) < 0.01
    assert abs(df.loc[df["date"] == "2026-01-03", "nav"].iloc[0] - (_NAV_BASE + 400.0)) < 0.01


def test_drawdown_calculation(engine):
    """Drawdown is correctly computed as (peak - nav) / peak."""
    hist_rows = [
        {
            "snapshot_date": "2026-01-01",
            "ticker": "AAPL",
            "direction": "LONG",
            "shares": 100.0,
            "price": 100.0,
            "market_value": 10000.0,
            "weight": 0.10,
            "unrealized_pnl": 0.0,
            "sector": "Technology",
            "combined_score": 80.0,
            "recorded_at": "2026-01-01T00:00:00",
        },
        {
            "snapshot_date": "2026-01-02",
            "ticker": "AAPL",
            "direction": "LONG",
            "shares": 100.0,
            "price": 110.0,
            "market_value": 11000.0,
            "weight": 0.10,
            "unrealized_pnl": 1000.0,
            "sector": "Technology",
            "combined_score": 80.0,
            "recorded_at": "2026-01-02T00:00:00",
        },
        {
            "snapshot_date": "2026-01-03",
            "ticker": "AAPL",
            "direction": "LONG",
            "shares": 100.0,
            "price": 90.0,
            "market_value": 9000.0,
            "weight": 0.10,
            "unrealized_pnl": -1000.0,
            "sector": "Technology",
            "combined_score": 80.0,
            "recorded_at": "2026-01-03T00:00:00",
        },
    ]
    with engine.begin() as conn:
        _insert_history(conn, hist_rows)

    df = build_nav_series(engine, nav_usd=_NAV_BASE)

    # Peak NAV is on day 2: _NAV_BASE + 1000
    peak_nav = _NAV_BASE + 1000.0
    nav_d3 = _NAV_BASE - 1000.0
    expected_dd = (peak_nav - nav_d3) / peak_nav

    actual_dd = float(df.loc[df["date"] == "2026-01-03", "drawdown_pct"].iloc[0])
    assert abs(actual_dd - expected_dd) < 1e-9


def test_nav_persisted_to_table(engine):
    """build_nav_series writes rows to portfolio_nav table."""
    hist_rows = [
        {
            "snapshot_date": "2026-02-01",
            "ticker": "MSFT",
            "direction": "LONG",
            "shares": 50.0,
            "price": 200.0,
            "market_value": 10000.0,
            "weight": 0.10,
            "unrealized_pnl": 250.0,
            "sector": "Technology",
            "combined_score": 75.0,
            "recorded_at": "2026-02-01T00:00:00",
        },
    ]
    with engine.begin() as conn:
        _insert_history(conn, hist_rows)

    build_nav_series(engine, nav_usd=_NAV_BASE)

    with engine.connect() as conn:
        count = conn.execute(sa.select(sa.func.count()).select_from(portfolio_nav)).scalar()
    assert count == 1
