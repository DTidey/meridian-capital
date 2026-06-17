"""Tests for risk/tail_risk.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import sqlalchemy as sa

import analysis.db  # noqa: F401
import factors.db  # noqa: F401
import portfolio.db  # noqa: F401
import risk.db  # noqa: F401
from data.db import daily_prices, initialise_schema
from portfolio.db import position_approvals
from risk.tail_risk import run_tail_risk

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCORE_DATE = "2026-03-15"
_VIX_TICKER = "^VIX"


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


def _base_config(vix_caution=25.0, vix_stress=35.0):
    return {
        "risk": {
            "tail_risk": {
                "vix_caution": vix_caution,
                "vix_stress": vix_stress,
                "credit_spread_sigma": 99.0,  # disabled for these tests
                "credit_lookback_days": 10,
            }
        }
    }


def _insert_vix(conn, vix_close, score_date=_SCORE_DATE):
    """Insert a single VIX price row."""
    conn.execute(
        daily_prices.insert().values(
            ticker=_VIX_TICKER,
            date=score_date,
            open=vix_close,
            high=vix_close,
            low=vix_close,
            close=vix_close,
            adj_close=vix_close,
            volume=0,
        )
    )
    conn.commit()


def _insert_approval(conn, ticker, action="BUY", target=300.0, current=0.0):
    conn.execute(
        position_approvals.insert().values(
            rebalance_date=_SCORE_DATE,
            ticker=ticker,
            action=action,
            target_shares=target,
            current_shares=current,
            delta_shares=target - current,
            status="APPROVED",
            created_at="2026-03-15T00:00:00",
        )
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTailRiskNormal:
    def test_vix_below_threshold_normal(self, mem_db, tmp_path):
        """VIX=20, no action, state='NORMAL'."""
        _insert_vix(mem_db, 20.0)
        result = run_tail_risk(mem_db, _SCORE_DATE, _base_config(), tmp_path, whatif=True)
        assert result["tail_risk_state"] == "NORMAL"
        assert result["actions"] == []


class TestTailRiskCaution:
    def test_vix_caution_reduces_20pct(self, mem_db, tmp_path):
        """VIX=28, REDUCE_GROSS_20 fires, target_shares *= 0.80."""
        _insert_vix(mem_db, 28.0)
        _insert_approval(mem_db, "AAPL", action="BUY", target=500)

        result = run_tail_risk(mem_db, _SCORE_DATE, _base_config(), tmp_path, whatif=False)
        assert result["tail_risk_state"] == "CAUTION"
        assert "REDUCE_GROSS_20" in result["actions"]

        row = mem_db.execute(
            sa.select(position_approvals.c.target_shares).where(
                position_approvals.c.ticker == "AAPL"
            )
        ).fetchone()
        assert row is not None
        assert row[0] == pytest.approx(400.0, abs=1.0)  # 500 * 0.80 = 400


class TestTailRiskStress:
    def test_vix_stress_reduces_50pct(self, mem_db, tmp_path):
        """VIX=38, REDUCE_GROSS_50 fires, target_shares *= 0.50."""
        _insert_vix(mem_db, 38.0)
        _insert_approval(mem_db, "MSFT", action="BUY", target=200)

        result = run_tail_risk(mem_db, _SCORE_DATE, _base_config(), tmp_path, whatif=False)
        assert result["tail_risk_state"] == "STRESS"
        assert "REDUCE_GROSS_50" in result["actions"]

        row = mem_db.execute(
            sa.select(position_approvals.c.target_shares).where(
                position_approvals.c.ticker == "MSFT"
            )
        ).fetchone()
        assert row is not None
        assert row[0] == pytest.approx(100.0, abs=1.0)  # 200 * 0.50 = 100


class TestTailRiskClosingTrades:
    def test_closing_trades_not_reduced(self, mem_db, tmp_path):
        """SELL trades are never resized regardless of VIX."""
        _insert_vix(mem_db, 38.0)
        # Insert a SELL (closing) trade
        _insert_approval(mem_db, "TSLA", action="SELL", target=0.0, current=300.0)

        run_tail_risk(mem_db, _SCORE_DATE, _base_config(), tmp_path, whatif=False)

        row = mem_db.execute(
            sa.select(position_approvals.c.target_shares).where(
                position_approvals.c.ticker == "TSLA"
            )
        ).fetchone()
        assert row is not None
        # SELL is a closing trade; target_shares=0 should be untouched
        assert row[0] == pytest.approx(0.0, abs=0.01)


class TestTailRiskWhatIf:
    def test_whatif_does_not_modify_db(self, mem_db, tmp_path):
        """VIX=38 but whatif=True, DB unchanged."""
        _insert_vix(mem_db, 38.0)
        _insert_approval(mem_db, "NVDA", action="BUY", target=400)

        run_tail_risk(mem_db, _SCORE_DATE, _base_config(), tmp_path, whatif=True)

        row = mem_db.execute(
            sa.select(position_approvals.c.target_shares).where(
                position_approvals.c.ticker == "NVDA"
            )
        ).fetchone()
        assert row is not None
        assert row[0] == pytest.approx(400.0)  # unchanged


class TestTailRiskMissingVIX:
    def test_missing_vix_returns_normal(self, mem_db, tmp_path):
        """No ^VIX data → state='NORMAL', no action."""
        # Do not insert any VIX price
        result = run_tail_risk(mem_db, _SCORE_DATE, _base_config(), tmp_path, whatif=True)
        assert result["tail_risk_state"] == "NORMAL"
        assert result["vix"] == pytest.approx(0.0)
        assert result["actions"] == []
