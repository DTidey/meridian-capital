"""Tests for risk/circuit_breakers.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from datetime import date, timedelta

import pytest
import sqlalchemy as sa

import analysis.db  # noqa: F401
import factors.db  # noqa: F401
import portfolio.db  # noqa: F401
import risk.db  # noqa: F401
from data.db import initialise_schema
from portfolio.db import portfolio_history, portfolio_positions, position_approvals
from risk.circuit_breakers import run_circuit_breakers
from risk.risk_state import default_state, is_halted

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCORE_DATE = "2026-03-05"
_NAV = 10_000_000.0


@pytest.fixture
def mem_engine():
    engine = sa.create_engine("sqlite:///:memory:", future=True)
    initialise_schema(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def mem_db(mem_engine):
    conn = mem_engine.connect()
    yield conn
    conn.close()


def _base_config(
    drawdown_kill=0.08,
    daily_close_all=0.025,
    daily_size_down=0.015,
    weekly_size_down=0.040,
    max_single_position_pct=0.03,
):
    return {
        "risk": {
            "circuit_breakers": {
                "drawdown_kill": drawdown_kill,
                "daily_close_all": daily_close_all,
                "daily_size_down": daily_size_down,
                "weekly_size_down": weekly_size_down,
                "max_single_position_pct": max_single_position_pct,
            }
        }
    }


def _insert_history(
    conn, snapshot_date, tickers, direction="LONG", market_value=400_000.0, unrealized_pnl=0.0
):
    """Insert portfolio_history rows for a snapshot date.

    unrealized_pnl drives NAV estimation: today_nav = nav_usd + sum(unrealized_pnl).
    Pass a negative value to simulate a portfolio loss.
    """
    now = "2026-03-05T00:00:00"
    rows = []
    pnl_per_ticker = unrealized_pnl / max(len(tickers), 1)
    for ticker in tickers:
        rows.append(
            {
                "snapshot_date": snapshot_date,
                "ticker": ticker,
                "direction": direction,
                "shares": 100.0,
                "price": market_value / 100.0,
                "market_value": market_value,
                "weight": market_value / 10_000_000.0,
                "unrealized_pnl": pnl_per_ticker,
                "sector": "Technology",
                "combined_score": 60.0,
                "recorded_at": now,
            }
        )
    conn.execute(portfolio_history.insert(), rows)
    conn.commit()


def _insert_approval(conn, ticker, action="BUY", target=200.0, current=0.0, status="APPROVED"):
    conn.execute(
        position_approvals.insert().values(
            rebalance_date=_SCORE_DATE,
            ticker=ticker,
            action=action,
            target_shares=target,
            current_shares=current,
            delta_shares=target - current,
            status=status,
            created_at="2026-03-05T00:00:00",
        )
    )
    conn.commit()


def _insert_position(conn, ticker, direction="LONG", shares=100.0, market_value=300_000.0):
    conn.execute(
        portfolio_positions.insert().values(
            ticker=ticker,
            direction=direction,
            shares=shares,
            entry_price=market_value / shares,
            entry_date="2026-01-01",
            current_price=market_value / shares,
            market_value=market_value,
            weight=market_value / _NAV,
            sector="Technology",
        )
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCircuitBreakerNormal:
    def test_normal_no_trigger(self, mem_db, tmp_path):
        """No P&L history → no trigger, state='NORMAL'."""
        state = default_state()
        result = run_circuit_breakers(
            mem_db, _SCORE_DATE, _NAV, _base_config(), state, tmp_path, whatif=True
        )
        assert result["circuit_breaker_state"] == "NORMAL"


class TestCircuitBreakerSizeDown:
    def test_size_down_daily_loss(self, mem_db, tmp_path):
        """Daily loss of -2.0% → SIZE_DOWN_30 fires, target_shares scaled to 70%."""
        # yesterday: unrealized_pnl=0 → nav=10M; today: pnl=-200k → nav=9.8M (-2.0%); drawdown 2%<8%
        yesterday = (date.fromisoformat(_SCORE_DATE) - timedelta(days=1)).isoformat()
        _insert_history(mem_db, yesterday, ["AAPL"], unrealized_pnl=0.0)
        _insert_history(mem_db, _SCORE_DATE, ["AAPL"], unrealized_pnl=-200_000.0)

        _insert_approval(mem_db, "MSFT", action="BUY", target=200, current=0)

        state = default_state()
        result = run_circuit_breakers(
            mem_db, _SCORE_DATE, _NAV, _base_config(), state, tmp_path, whatif=False
        )
        assert result["circuit_breaker_state"] == "SIZE_DOWN"

        # target_shares should be scaled to 70% of original 200 = 140
        row = mem_db.execute(
            sa.select(position_approvals.c.target_shares).where(
                position_approvals.c.ticker == "MSFT"
            )
        ).fetchone()
        assert row is not None
        assert row[0] == pytest.approx(140.0, abs=1.0)


class TestCircuitBreakerCloseAll:
    def test_close_all_large_daily_loss(self, mem_db, tmp_path):
        """Daily loss of -3.0% → CLOSE_ALL, APPROVED trades become REJECTED."""
        # yesterday: pnl=0 → nav=10M; today: pnl=-300k → nav=9.7M (-3%); drawdown 3% < 8%
        yesterday = (date.fromisoformat(_SCORE_DATE) - timedelta(days=1)).isoformat()
        _insert_history(mem_db, yesterday, ["AAPL"], unrealized_pnl=0.0)
        _insert_history(mem_db, _SCORE_DATE, ["AAPL"], unrealized_pnl=-300_000.0)

        _insert_approval(mem_db, "MSFT", action="BUY", target=200, current=0)
        _insert_approval(mem_db, "GOOG", action="BUY", target=100, current=0)

        state = default_state()
        result = run_circuit_breakers(
            mem_db, _SCORE_DATE, _NAV, _base_config(), state, tmp_path, whatif=False
        )
        assert result["circuit_breaker_state"] == "CLOSE_ALL"

        rows = mem_db.execute(
            sa.select(position_approvals.c.status).where(
                position_approvals.c.rebalance_date == _SCORE_DATE
            )
        ).fetchall()
        statuses = [r[0] for r in rows]
        assert all(s == "REJECTED" for s in statuses)


class TestCircuitBreakerKillSwitch:
    def test_drawdown_kill_switch(self, mem_db, tmp_path):
        """High peak_nav in risk_state, current nav = 10M → drawdown ~17% > 8% threshold."""
        # peak_nav=12M set in risk_state; today pnl=0 → nav=10M; drawdown=(12-10)/12≈17%
        _insert_history(mem_db, _SCORE_DATE, ["AAPL"], unrealized_pnl=0.0)

        state = default_state()
        state["peak_nav_usd"] = 12_000_000.0

        result = run_circuit_breakers(
            mem_db, _SCORE_DATE, _NAV, _base_config(), state, tmp_path, whatif=False
        )
        assert result["circuit_breaker_state"] == "KILL_SWITCH"
        assert is_halted(tmp_path)


class TestCircuitBreakerForceClose:
    def test_force_close_oversized(self, mem_db, tmp_path):
        """Position with market_value = 4% of NAV triggers FORCE_CLOSE."""
        # max_single_position_pct=0.03; inserting a position at 4% of NAV
        _insert_position(mem_db, "BIGG", direction="LONG", shares=100, market_value=400_000.0)

        state = default_state()
        _result = run_circuit_breakers(
            mem_db,
            _SCORE_DATE,
            _NAV,
            _base_config(max_single_position_pct=0.03),
            state,
            tmp_path,
            whatif=False,
        )
        # FORCE_CLOSE fires independently; state may still be NORMAL if no P&L triggers
        # Verify a closing SELL trade was inserted
        rows = mem_db.execute(
            sa.select(position_approvals.c.action, position_approvals.c.status).where(
                position_approvals.c.ticker == "BIGG"
            )
        ).fetchall()
        assert any(r[0] == "SELL" and r[1] == "APPROVED" for r in rows)


class TestCircuitBreakerWhatIf:
    def test_whatif_no_db_changes(self, mem_db, tmp_path):
        """whatif=True with SIZE_DOWN trigger — position_approvals unchanged."""
        # yesterday: pnl=0 → nav=10M; today: pnl=-200k → nav=9.8M (-2%); drawdown 2%<8%
        yesterday = (date.fromisoformat(_SCORE_DATE) - timedelta(days=1)).isoformat()
        _insert_history(mem_db, yesterday, ["AAPL"], unrealized_pnl=0.0)
        _insert_history(mem_db, _SCORE_DATE, ["AAPL"], unrealized_pnl=-200_000.0)

        _insert_approval(mem_db, "TSLA", action="BUY", target=300, current=0)

        state = default_state()
        result = run_circuit_breakers(
            mem_db, _SCORE_DATE, _NAV, _base_config(), state, tmp_path, whatif=True
        )
        assert result["circuit_breaker_state"] == "SIZE_DOWN"

        # DB should be unchanged — target_shares still 300
        row = mem_db.execute(
            sa.select(position_approvals.c.target_shares, position_approvals.c.status).where(
                position_approvals.c.ticker == "TSLA"
            )
        ).fetchone()
        assert row is not None
        assert row[0] == pytest.approx(300.0)
        assert row[1] == "APPROVED"
