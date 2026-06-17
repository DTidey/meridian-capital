"""Tests for risk/pre_trade.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import portfolio.db  # noqa: F401 — registers portfolio tables on shared metadata
import factors.db    # noqa: F401 — registers factor_scores table
import risk.db       # noqa: F401 — registers risk_log table
import analysis.db   # noqa: F401 — registers analysis tables

from datetime import date, timedelta

import pandas as pd
import pytest
import sqlalchemy as sa

from data.db import daily_prices, earnings_calendar, initialise_schema
from factors.db import factor_scores as factor_scores_table
from portfolio.db import portfolio_positions, position_approvals
from risk.pre_trade import run_pre_trade
from risk.risk_state import set_halt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCORE_DATE = "2026-03-01"
_NAV = 10_000_000


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


def _insert_prices(conn, ticker, n=25, start="2026-01-01", base=100.0):
    """Insert n days of synthetic OHLCV prices for ticker."""
    d = date.fromisoformat(start)
    rows = []
    for i in range(n):
        p = base + i * 0.5
        rows.append({
            "ticker":    ticker,
            "date":      str(d + timedelta(days=i)),
            "adj_close": p,
            "open":      p,
            "high":      p * 1.01,
            "low":       p * 0.99,
            "close":     p,
            "volume":    500_000,
        })
    conn.execute(daily_prices.insert(), rows)
    conn.commit()


def _insert_factor_score(conn, ticker, sector="Technology", mom=50.0):
    conn.execute(factor_scores_table.insert().values(
        ticker=ticker,
        score_date=_SCORE_DATE,
        sector=sector,
        momentum_score=mom,
        quality_score=50.0,
        value_score=50.0,
        revisions_score=50.0,
        insider_score=50.0,
        growth_score=50.0,
        short_interest_score=50.0,
        institutional_score=50.0,
        composite_score=50.0,
    ))
    conn.commit()


def _insert_position(conn, ticker, direction="LONG", shares=400.0, weight=0.04, sector="Technology"):
    conn.execute(portfolio_positions.insert().values(
        ticker=ticker,
        direction=direction,
        shares=shares,
        entry_price=100.0,
        entry_date="2026-01-01",
        current_price=100.0,
        market_value=shares * 100.0,
        weight=weight,
        sector=sector,
    ))
    conn.commit()


def _insert_approval(conn, ticker, action="BUY", target=200.0, current=0.0, rebalance_date=_SCORE_DATE):
    result = conn.execute(position_approvals.insert().values(
        rebalance_date=rebalance_date,
        ticker=ticker,
        action=action,
        target_shares=target,
        current_shares=current,
        delta_shares=target - current,
        status="PENDING",
        created_at="2026-03-01T00:00:00",
    ))
    conn.commit()
    return result.lastrowid


def _base_config():
    return {
        "portfolio": {"nav_usd": _NAV},
        "risk": {
            "pre_trade": {
                "adv_lookback":           20,
                "adv_pct":                0.10,
                "max_position_pct":       0.10,
                "max_sector_pct":         0.50,
                "max_gross":              1.65,
                "net_min":                -0.15,
                "net_max":                0.30,
                "max_net_beta":           0.50,
                "max_pairwise_corr":      0.90,
                "corr_lookback":          60,
                "earnings_blackout_days": 5,
            }
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPreTradeHalt:
    def test_halt_rejects_all(self, mem_db, tmp_path):
        """After set_halt, run_pre_trade rejects all non-closing opening trades."""
        _insert_prices(mem_db, "AAPL")
        _insert_prices(mem_db, "MSFT")
        _insert_factor_score(mem_db, "AAPL")
        _insert_factor_score(mem_db, "MSFT")
        _insert_approval(mem_db, "AAPL", action="BUY", target=100)
        _insert_approval(mem_db, "MSFT", action="BUY", target=100)

        set_halt(tmp_path)
        result = run_pre_trade(mem_db, _SCORE_DATE, _base_config(), tmp_path, whatif=True)
        assert len(result) == 2
        assert (result["result"] == "REJECTED").all()
        assert result["reason"].str.contains("HALT_LOCK").all()

    def test_closing_always_approved(self, mem_db, tmp_path):
        """A SELL trade passes even with halt lock active."""
        _insert_prices(mem_db, "AAPL")
        _insert_factor_score(mem_db, "AAPL")
        _insert_approval(mem_db, "AAPL", action="SELL", target=0, current=200)

        set_halt(tmp_path)
        result = run_pre_trade(mem_db, _SCORE_DATE, _base_config(), tmp_path, whatif=True)
        assert len(result) == 1
        assert result.iloc[0]["result"] == "APPROVED"


class TestPreTradeEarningsBlackout:
    def test_earnings_blackout_reduces_size(self, mem_db, tmp_path):
        """Ticker with earnings in 3 days gets 50% size cut (BLACKOUT_REDUCED)."""
        _insert_prices(mem_db, "AAPL")
        _insert_factor_score(mem_db, "AAPL")
        _insert_approval(mem_db, "AAPL", action="BUY", target=200, current=0)

        # Earnings 3 days from score_date — inside default blackout of 5d
        earnings_date = (date.fromisoformat(_SCORE_DATE) + timedelta(days=3)).isoformat()
        mem_db.execute(earnings_calendar.insert().values(
            ticker="AAPL",
            earnings_date=earnings_date,
            eps_estimate=None,
        ))
        mem_db.commit()

        result = run_pre_trade(mem_db, _SCORE_DATE, _base_config(), tmp_path, whatif=True)
        assert len(result) == 1
        row = result.iloc[0]
        assert row["result"] == "APPROVED"
        assert "BLACKOUT_REDUCED" in row["reason"]


class TestPreTradeNoPending:
    def test_no_pending_returns_empty(self, mem_db, tmp_path):
        """Returns empty DataFrame if no PENDING rows."""
        result = run_pre_trade(mem_db, _SCORE_DATE, _base_config(), tmp_path)
        assert result.empty
        assert list(result.columns) == ["ticker", "action", "result", "reason"]


class TestPreTradeApproved:
    def test_approved_in_normal_conditions(self, mem_db, tmp_path):
        """With no issues, trades get APPROVED status."""
        _insert_prices(mem_db, "AAPL")
        _insert_factor_score(mem_db, "AAPL")
        _insert_approval(mem_db, "AAPL", action="BUY", target=100, current=0)

        result = run_pre_trade(mem_db, _SCORE_DATE, _base_config(), tmp_path, whatif=True)
        assert len(result) == 1
        assert result.iloc[0]["result"] == "APPROVED"


class TestPreTradeWhatIf:
    def test_whatif_does_not_write(self, mem_db, tmp_path):
        """whatif=True does not update position_approvals in DB."""
        _insert_prices(mem_db, "AAPL")
        _insert_factor_score(mem_db, "AAPL")
        approval_id = _insert_approval(mem_db, "AAPL", action="BUY", target=100)

        run_pre_trade(mem_db, _SCORE_DATE, _base_config(), tmp_path, whatif=True)

        # Status in DB should still be PENDING
        row = mem_db.execute(
            sa.select(position_approvals.c.status).where(
                position_approvals.c.id == approval_id
            )
        ).fetchone()
        assert row is not None
        assert row[0] == "PENDING"
