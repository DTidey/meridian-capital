"""Tests for portfolio/rebalance_schedule.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from datetime import date, timedelta

import portfolio.db  # noqa: F401
from data.db import earnings_calendar
from portfolio.rebalance_schedule import _third_friday, check_events

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_earnings(conn, ticker, earnings_date, eps_estimate=0.5):
    conn.execute(
        earnings_calendar.insert().values(
            ticker=ticker,
            earnings_date=str(earnings_date),
            eps_estimate=eps_estimate,
            fetched_at="2026-01-01T00:00:00+00:00",
        )
    )
    conn.commit()


# ---------------------------------------------------------------------------
# check_events — earnings
# ---------------------------------------------------------------------------


class TestEarningsWarnings:
    def test_no_warnings_when_clear(self, tmp_db):
        """No earnings, score_date far from FOMC/opex → no warnings."""
        # 2026-02-15 is far from any FOMC (next is 2026-03-18)
        # and far from the Feb opex (third Friday of Feb 2026 = Feb 20, delta=5 days,
        # which is exactly on the boundary; pick a date clearly safe)
        # Use 2026-02-10: far from FOMC (2026-01-28 delta=13, 2026-03-18 delta=36)
        # Feb third Friday = Feb 20, delta=10 → > 3
        warnings = check_events([], "2026-02-10", tmp_db)
        _fomc_or_opex = [w for w in warnings if "FOMC" in w or "options expiration" in w]
        earnings_warns = [w for w in warnings if "earnings" in w.lower()]
        assert earnings_warns == []

    def test_earnings_within_two_days_warns(self, tmp_db):
        score_date = "2026-03-01"
        earn_date = date.fromisoformat(score_date) + timedelta(days=1)
        _insert_earnings(tmp_db, "AAPL", earn_date)
        warnings = check_events(["AAPL"], score_date, tmp_db)
        assert any("AAPL" in w for w in warnings)

    def test_earnings_outside_window_no_warn(self, tmp_db):
        score_date = "2026-03-01"
        earn_date = date.fromisoformat(score_date) + timedelta(days=10)
        _insert_earnings(tmp_db, "GOOG", earn_date)
        warnings = check_events(["GOOG"], score_date, tmp_db)
        earnings_warns = [w for w in warnings if "GOOG" in w]
        assert earnings_warns == []

    def test_no_tickers_no_earnings_warning(self, tmp_db):
        _insert_earnings(tmp_db, "AAPL", date(2026, 3, 2))
        warnings = check_events([], "2026-03-01", tmp_db)
        earnings_warns = [w for w in warnings if "AAPL" in w]
        assert earnings_warns == []


# ---------------------------------------------------------------------------
# check_events — FOMC
# ---------------------------------------------------------------------------


class TestFomcWarnings:
    def test_fomc_within_five_days_warns(self, tmp_db):
        """2026-01-25 is 3 days before FOMC on 2026-01-28 → warning."""
        warnings = check_events([], "2026-01-25", tmp_db)
        assert any("FOMC" in w for w in warnings)

    def test_fomc_outside_window_no_warn(self, tmp_db):
        """2026-02-15 is far from 2026-01-28 (delta=18) and 2026-03-18 (delta=31)."""
        warnings = check_events([], "2026-02-15", tmp_db)
        fomc_warns = [w for w in warnings if "FOMC" in w]
        assert fomc_warns == []


# ---------------------------------------------------------------------------
# check_events — options expiry
# ---------------------------------------------------------------------------


class TestOptionsExpiryWarnings:
    def test_options_expiry_within_three_days_warns(self, tmp_db):
        """Use a score_date 2 days before the third Friday of a month."""
        # January 2026 third Friday = 2026-01-16
        # score_date = 2026-01-14 (2 days before)
        # But that's close to FOMC 2026-01-28 (delta=14, fine)
        score_date = "2026-01-14"
        warnings = check_events([], score_date, tmp_db)
        assert any("options expiration" in w for w in warnings)

    def test_options_expiry_far_gives_no_warn(self, tmp_db):
        """2026-02-10 is 10 days before Feb 20 opex → no opex warning."""
        warnings = check_events([], "2026-02-10", tmp_db)
        opex_warns = [w for w in warnings if "options expiration" in w]
        assert opex_warns == []


# ---------------------------------------------------------------------------
# _third_friday
# ---------------------------------------------------------------------------


class TestThirdFriday:
    def test_january_2026(self):
        """Third Friday of January 2026 should be 2026-01-16."""
        assert _third_friday(2026, 1) == date(2026, 1, 16)

    def test_result_is_always_friday(self):
        for month in range(1, 13):
            d = _third_friday(2026, month)
            assert d.weekday() == 4, f"Expected Friday for month {month}, got {d.strftime('%A')}"

    def test_february_2026(self):
        """Third Friday of February 2026 should be 2026-02-20."""
        assert _third_friday(2026, 2) == date(2026, 2, 20)
