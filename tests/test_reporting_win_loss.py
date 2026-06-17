"""Tests for reporting/win_loss.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

import analysis.db  # noqa: F401
import execution.db  # noqa: F401
import factors.db  # noqa: F401
import portfolio.db  # noqa: F401
import reporting.db  # noqa: F401
import risk.db  # noqa: F401
from data.db import initialise_schema
from reporting.db import position_trades
from reporting.win_loss import _stats, _streaks, compute


@pytest.fixture
def engine(tmp_path):
    from data.db import get_engine

    eng = get_engine(f"sqlite:///{tmp_path / 'test.db'}")
    initialise_schema(eng)
    yield eng
    eng.dispose()


def _insert_trades(conn, trades):
    conn.execute(position_trades.insert(), trades)


def _make_trade(
    ticker,
    direction,
    pnl,
    holding_days=10,
    sector="Tech",
    entry_vix=18.0,
    entry_score=75.0,
    entry_date="2026-01-01",
    exit_date="2026-01-11",
    entry_price=100.0,
    exit_price=110.0,
    shares=100.0,
):
    return {
        "ticker": ticker,
        "direction": direction,
        "entry_date": entry_date,
        "exit_date": exit_date,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "shares": shares,
        "realized_pnl": pnl,
        "holding_days": holding_days,
        "sector": sector,
        "entry_score": entry_score,
        "entry_vix": entry_vix,
    }


def test_win_rate_all_winners(engine):
    trades = [_make_trade(f"T{i}", "LONG", 100.0) for i in range(5)]
    with engine.begin() as conn:
        _insert_trades(conn, trades)

    result = compute(engine)
    assert result["overall"]["win_rate"] == 1.0
    assert result["overall"]["total_trades"] == 5


def test_win_rate_no_trades(engine):
    result = compute(engine)
    assert result["overall"]["win_rate"] == 0.0
    assert result["overall"]["total_trades"] == 0
    # Must not raise KeyError
    assert "by_side" in result
    assert "streaks" in result


def test_win_rate_mixed(engine):
    trades = [
        _make_trade("A", "LONG", 200.0),
        _make_trade("B", "LONG", -100.0),
        _make_trade("C", "LONG", 150.0),
        _make_trade("D", "LONG", -50.0),
    ]
    with engine.begin() as conn:
        _insert_trades(conn, trades)

    result = compute(engine)
    assert result["overall"]["win_rate"] == pytest.approx(0.5, abs=1e-4)
    assert result["overall"]["total_trades"] == 4


def test_holding_period_bucketing(engine):
    trades = [
        _make_trade("A", "LONG", 100.0, holding_days=2),  # 1-5d
        _make_trade("B", "LONG", 100.0, holding_days=10),  # 5-20d
        _make_trade("C", "LONG", 100.0, holding_days=50),  # 20-60d
    ]
    with engine.begin() as conn:
        _insert_trades(conn, trades)

    result = compute(engine)
    bp = result["by_holding_period"]
    assert bp["1-5d"]["total_trades"] == 1
    assert bp["5-20d"]["total_trades"] == 1
    assert bp["20-60d"]["total_trades"] == 1
    assert bp["60d+"]["total_trades"] == 0


def test_stats_pl_ratio():
    """P/L ratio = |avg_win / avg_loss|."""
    import pandas as pd

    df = pd.DataFrame(
        [
            {"realized_pnl": 200.0, "win": True},
            {"realized_pnl": 100.0, "win": True},
            {"realized_pnl": -100.0, "win": False},
        ]
    )
    s = _stats(df)
    assert s["pl_ratio"] == pytest.approx(1.5, abs=1e-4)


def test_streak_detection():
    """Streak tracker correctly identifies win/loss runs."""
    import pandas as pd

    df = pd.DataFrame(
        {
            "exit_date": ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05"],
            "win": [True, True, False, False, False],
            "realized_pnl": [100, 50, -20, -30, -10],
        }
    )
    s = _streaks(df)
    assert s["longest_win_streak"] == 2
    assert s["longest_loss_streak"] == 3
    assert "losses" in s["current_streak"]
