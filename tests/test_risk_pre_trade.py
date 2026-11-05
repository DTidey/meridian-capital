"""Tests for risk/pre_trade.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from datetime import date, timedelta

import pytest
import sqlalchemy as sa

import analysis.db  # noqa: F401 — registers analysis tables
import factors.db  # noqa: F401 — registers factor_scores table
import portfolio.db  # noqa: F401 — registers portfolio tables on shared metadata
import risk.db  # noqa: F401 — registers risk_log table
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
        rows.append(
            {
                "ticker": ticker,
                "date": str(d + timedelta(days=i)),
                "adj_close": p,
                "open": p,
                "high": p * 1.01,
                "low": p * 0.99,
                "close": p,
                "volume": 500_000,
            }
        )
    conn.execute(daily_prices.insert(), rows)
    conn.commit()


def _insert_factor_score(conn, ticker, sector="Technology", mom=50.0):
    conn.execute(
        factor_scores_table.insert().values(
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
        )
    )
    conn.commit()


def _insert_position(
    conn, ticker, direction="LONG", shares=400.0, weight=0.04, sector="Technology"
):
    conn.execute(
        portfolio_positions.insert().values(
            ticker=ticker,
            direction=direction,
            shares=shares,
            entry_price=100.0,
            entry_date="2026-01-01",
            current_price=100.0,
            market_value=shares * 100.0,
            weight=weight,
            sector=sector,
        )
    )
    conn.commit()


def _insert_approval(
    conn, ticker, action="BUY", target=200.0, current=0.0, rebalance_date=_SCORE_DATE
):
    result = conn.execute(
        position_approvals.insert().values(
            rebalance_date=rebalance_date,
            ticker=ticker,
            action=action,
            target_shares=target,
            current_shares=current,
            delta_shares=target - current,
            status="PENDING",
            created_at="2026-03-01T00:00:00",
        )
    )
    conn.commit()
    return result.lastrowid


def _base_config():
    return {
        "portfolio": {"nav_usd": _NAV},
        "risk": {
            "pre_trade": {
                "adv_lookback": 20,
                "adv_pct": 0.10,
                "max_position_pct": 0.10,
                "max_sector_pct": 0.50,
                "max_gross": 1.65,
                "net_min": -0.15,
                "net_max": 0.30,
                "max_net_beta": 0.50,
                "max_pairwise_corr": 0.90,
                "corr_lookback": 60,
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


def _approval_row(conn, approval_id):
    return conn.execute(
        sa.select(position_approvals).where(position_approvals.c.id == approval_id)
    ).fetchone()


def _insert_earnings(conn, ticker, days_from_score):
    earnings_date = (date.fromisoformat(_SCORE_DATE) + timedelta(days=days_from_score)).isoformat()
    conn.execute(earnings_calendar.insert().values(ticker=ticker, earnings_date=earnings_date))
    conn.commit()


class TestEarningsWindow:
    """Packet 58: Layer 4 owns the earnings haircut; pre-trade no longer resizes."""

    def _run(self, conn, tmp_path, action, target, current, days, ticker="AVY", cfg=None):
        _insert_prices(conn, ticker)
        _insert_factor_score(conn, ticker)
        if days is not None:
            _insert_earnings(conn, ticker, days)
        approval_id = _insert_approval(conn, ticker, action=action, target=target, current=current)
        result = run_pre_trade(conn, _SCORE_DATE, cfg or _base_config(), tmp_path, whatif=False)
        return result.iloc[0], _approval_row(conn, approval_id)

    def test_no_resize_reason_tagged(self, mem_db, tmp_path):
        res, row = self._run(mem_db, tmp_path, "BUY", target=200, current=0, days=3)
        assert res["result"] == "APPROVED"
        assert "EARNINGS_WINDOW" in res["reason"]
        assert (row.action, row.target_shares, row.delta_shares) == ("BUY", 200, 200)
        assert row.status == "APPROVED"

    def test_add_to_held_not_flipped(self, mem_db, tmp_path):
        # Packet 56 used to turn this add (7 -> 10, halved to 5) into SELL 2.
        _insert_position(mem_db, "AVY", shares=7.0, weight=0.0001)
        res, row = self._run(mem_db, tmp_path, "BUY", target=10, current=7, days=3)
        assert res["result"] == "APPROVED"
        assert (row.action, row.target_shares, row.delta_shares) == ("BUY", 10, 3)

    def test_short_add_not_flipped(self, mem_db, tmp_path):
        _insert_position(mem_db, "AVY", direction="SHORT", shares=-14.0, weight=-0.0001)
        _, row = self._run(mem_db, tmp_path, "SHORT", target=-20, current=-14, days=3)
        assert (row.action, row.target_shares, row.delta_shares) == ("SHORT", -20, -6)

    def test_window_name_still_size_checked(self, mem_db, tmp_path):
        # 15,000 shares x ~$112 = 16.8% of NAV > 10% cap: the old early return approved it.
        cfg = _base_config()
        cfg["risk"]["pre_trade"]["adv_pct"] = 1000.0  # isolate the size check
        res, row = self._run(mem_db, tmp_path, "BUY", target=15_000, current=0, days=3, cfg=cfg)
        assert res["result"] == "REJECTED"
        assert "POSITION_SIZE" in res["reason"]
        assert row.status == "REJECTED"

    def test_reported_yesterday_not_tagged(self, mem_db, tmp_path):
        res, row = self._run(mem_db, tmp_path, "BUY", target=200, current=0, days=-1)
        assert res["result"] == "APPROVED"
        assert "EARNINGS_WINDOW" not in res["reason"]
        assert (row.action, row.target_shares) == ("BUY", 200)

    def test_earnings_today_tagged(self, mem_db, tmp_path):
        res, _ = self._run(mem_db, tmp_path, "BUY", target=200, current=0, days=0)
        assert "EARNINGS_WINDOW" in res["reason"]

    def test_earnings_after_window_not_tagged(self, mem_db, tmp_path):
        res, _ = self._run(mem_db, tmp_path, "BUY", target=200, current=0, days=6)
        assert "EARNINGS_WINDOW" not in res["reason"]

    def test_closing_trade_unaffected(self, mem_db, tmp_path):
        _insert_position(mem_db, "AVY", shares=300.0)
        res, row = self._run(mem_db, tmp_path, "SELL", target=0, current=300, days=2)
        assert res["result"] == "APPROVED"
        assert "CLOSING" in res["reason"]
        assert (row.action, row.target_shares) == ("SELL", 0)


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
            sa.select(position_approvals.c.status).where(position_approvals.c.id == approval_id)
        ).fetchone()
        assert row is not None
        assert row[0] == "PENDING"


class TestTargetBook:
    """Packet 44: each trade is checked against the whole proposed book."""

    def _setup(self, conn, trades):
        # Prices end at 112.0; 8,000 shares = $896,000 = 8.96% of the $10M NAV.
        for i, (ticker, action, target) in enumerate(trades):
            _insert_prices(conn, ticker)
            _insert_factor_score(conn, ticker, sector=f"Sector{i}")
            _insert_approval(conn, ticker, action=action, target=target)

    def test_net_check_sees_whole_proposed_book(self, mem_db, tmp_path):
        # Four longs at ~9% each: net ~0.36 > 0.30. One at a time against an empty broker
        # book each would pass; against the proposed book each is over the limit.
        self._setup(mem_db, [(t, "BUY", 8000) for t in ("A", "B", "C", "D")])
        result = run_pre_trade(mem_db, _SCORE_DATE, _base_config(), tmp_path, whatif=True)
        assert result["reason"].str.startswith("NET_EXPOSURE").all()

    def test_offsetting_shorts_count_toward_net(self, mem_db, tmp_path):
        trades = [(t, "BUY", 8000) for t in ("A", "B", "C", "D")]
        trades += [(t, "SHORT", -8000) for t in ("E", "F")]
        self._setup(mem_db, trades)
        result = run_pre_trade(mem_db, _SCORE_DATE, _base_config(), tmp_path, whatif=True)
        assert not result["reason"].str.startswith("NET_EXPOSURE").any()

    def test_new_position_not_correlated_with_itself(self, mem_db, tmp_path):
        # Adversarial: the proposed book contains the trade's own ticker.
        self._setup(mem_db, [("A", "BUY", 8000)])
        result = run_pre_trade(mem_db, _SCORE_DATE, _base_config(), tmp_path, whatif=True)
        assert result.iloc[0]["result"] == "APPROVED", result.iloc[0]["reason"]

    def test_null_position_weight_does_not_crash(self, mem_db, tmp_path):
        # Adversarial: a synced position with no weight (zero equity) counts as 0.
        _insert_position(mem_db, "HELD", weight=None)
        self._setup(mem_db, [("A", "BUY", 8000)])
        result = run_pre_trade(mem_db, _SCORE_DATE, _base_config(), tmp_path, whatif=True)
        assert len(result) == 1
