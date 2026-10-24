"""Packet 50: turnover from filled orders, not from snapshot market-value changes."""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

import analysis.db  # noqa: F401
import execution.db  # noqa: F401
import factors.db  # noqa: F401
import portfolio.db  # noqa: F401
import reporting.db  # noqa: F401
import risk.db  # noqa: F401
from data.db import get_engine, initialise_schema
from execution.db import execution_orders
from portfolio.db import portfolio_history
from reporting.db import portfolio_nav
from reporting.turnover import compute


def _days_ago(n):
    return (date.today() - timedelta(days=n)).isoformat()


@pytest.fixture
def engine(tmp_path):
    eng = get_engine(f"sqlite:///{tmp_path / 't.db'}")
    initialise_schema(eng)
    with eng.begin() as conn:
        conn.execute(
            portfolio_nav.insert(),
            [{"date": _days_ago(n), "nav": 100_000.0} for n in range(0, 60, 5)],
        )
    yield eng
    eng.dispose()


def _order(conn, days_ago, shares, price, status="FILLED", ticker="AAPL"):
    conn.execute(
        execution_orders.insert().values(
            rebalance_date=_days_ago(days_ago),
            ticker=ticker,
            action="BUY",
            ordered_shares=abs(shares),
            filled_shares=shares,
            avg_fill_price=price,
            status=status,
        )
    )


def test_turnover_from_fills(engine):
    with engine.begin() as conn:
        _order(conn, 3, 100, 200.0)  # $20,000
        _order(conn, 10, 50, 400.0, ticker="MSFT")  # $20,000
        _order(conn, 45, 100, 100.0, ticker="XOM")  # $10,000, outside 30 days
    t = compute(engine)
    assert t["turnover_30d_pct"] == pytest.approx(40_000 / 2 / 100_000)
    assert t["turnover_90d_pct"] == pytest.approx(50_000 / 2 / 100_000)


def test_partial_fill_on_cancelled_order_counts(engine):
    with engine.begin() as conn:
        _order(conn, 3, 40, 100.0, status="CANCELLED")
    assert compute(engine)["turnover_30d_pct"] == pytest.approx(4_000 / 2 / 100_000)


def test_unfilled_orders_do_not_count(engine):
    with engine.begin() as conn:
        _order(conn, 3, 0, None, status="PENDING")
    assert compute(engine)["turnover_30d_pct"] == 0.0


def test_price_drift_is_not_turnover(engine):
    # Adversarial: the same holding re-snapshotted at a new price is not a trade.
    with engine.begin() as conn:
        for n, mv in [(4, 10_000.0), (3, 12_000.0), (2, 9_000.0)]:
            conn.execute(
                portfolio_history.insert().values(
                    snapshot_date=_days_ago(n), ticker="AAPL", market_value=mv
                )
            )
    assert compute(engine)["turnover_30d_pct"] == 0.0
