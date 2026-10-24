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


@pytest.fixture
def engine(tmp_path):
    from data.db import get_engine

    eng = get_engine(f"sqlite:///{tmp_path / 'test.db'}")
    initialise_schema(eng)
    yield eng
    eng.dispose()


def _insert_navs(conn, navs):
    """navs: {date: nav}, as recorded by the nightly book sync."""
    conn.execute(portfolio_nav.insert(), [{"date": d, "nav": n} for d, n in navs.items()])


def _insert_spy(conn, rows):
    conn.execute(daily_prices.insert(), rows)


def _navs(engine):
    with engine.connect() as conn:
        rows = conn.execute(sa.select(portfolio_nav).order_by(portfolio_nav.c.date)).fetchall()
    return {r.date: r for r in rows}


def test_build_nav_series_empty(engine):
    """No recorded NAV → empty DataFrame, no crash."""
    df = build_nav_series(engine)
    assert df.empty


def test_recorded_nav_never_changed(engine):
    """Packet 50: NAV is Alpaca equity; the series step must not recompute it."""
    with engine.begin() as conn:
        _insert_navs(conn, {"2026-01-01": 100_000.0, "2026-01-02": 101_500.0})
        # Adversarial: history P&L used to be added to a config NAV; it must be ignored.
        conn.execute(
            portfolio_history.insert().values(
                snapshot_date="2026-01-02", ticker="AAPL", unrealized_pnl=-50_000.0
            )
        )
    df = build_nav_series(engine)
    assert df["nav"].tolist() == [100_000.0, 101_500.0]
    stored = _navs(engine)
    assert stored["2026-01-02"].nav == pytest.approx(101_500.0)


def test_drawdown_calculation(engine):
    """Drawdown = (running peak - nav) / running peak."""
    with engine.begin() as conn:
        _insert_navs(conn, {"2026-01-01": 100.0, "2026-01-02": 110.0, "2026-01-03": 99.0})
    build_nav_series(engine)
    stored = _navs(engine)
    assert stored["2026-01-01"].drawdown_pct == pytest.approx(0.0)
    assert stored["2026-01-02"].drawdown_pct == pytest.approx(0.0)
    assert stored["2026-01-03"].drawdown_pct == pytest.approx((110.0 - 99.0) / 110.0)


def test_spy_close_filled(engine):
    with engine.begin() as conn:
        _insert_navs(conn, {"2026-01-01": 100.0, "2026-01-02": 101.0})
        _insert_spy(conn, [{"ticker": "SPY", "date": "2026-01-02", "adj_close": 480.5}])
    build_nav_series(engine)
    stored = _navs(engine)
    assert stored["2026-01-02"].spy_close == pytest.approx(480.5)
    assert stored["2026-01-01"].spy_close is None


def test_no_rows_added(engine):
    """History snapshots without a recorded NAV do not create NAV rows."""
    with engine.begin() as conn:
        conn.execute(portfolio_history.insert().values(snapshot_date="2026-02-01", ticker="MSFT"))
    build_nav_series(engine)
    with engine.connect() as conn:
        count = conn.execute(sa.select(sa.func.count()).select_from(portfolio_nav)).scalar()
    assert count == 0
