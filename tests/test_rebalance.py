"""Tests for portfolio/rebalance.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import portfolio.db  # noqa: F401

import pandas as pd
import pytest

from portfolio.rebalance import generate_trades


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CONFIG = {
    "portfolio": {
        "nav_usd": 1_000_000,
        "turnover_budget_pct": 0.20,
        "transaction_costs": {
            "spread_hl_fraction": 0.05,
            "market_impact_coef": 0.10,
        },
        "adv_lookback_days": 5,
    }
}


def _make_price_df(n=10, close=100.0, high=101.0, low=99.0, volume=10_000):
    return pd.DataFrame({
        "close":  [float(close)]  * n,
        "high":   [float(high)]   * n,
        "low":    [float(low)]    * n,
        "volume": [float(volume)] * n,
    })


def _prices_for(*tickers):
    return {t: _make_price_df() for t in tickers}


def _empty_current():
    return pd.DataFrame(columns=["ticker", "shares"])


def _current_df(rows):
    """rows: list of (ticker, shares)"""
    return pd.DataFrame(rows, columns=["ticker", "shares"])


def _target_df(rows):
    """rows: list of dicts with keys matching target columns."""
    defaults = {
        "direction":      "LONG",
        "combined_score": 75.0,
        "current_price":  100.0,
        "sector":         "Technology",
        "beta":           1.0,
        "weight":         0.05,
    }
    records = []
    for row in rows:
        r = dict(defaults)
        r.update(row)
        records.append(r)
    return pd.DataFrame(records)


def _run(current, target, prices=None, config=None, conn=None, score_date="2026-03-01"):
    if prices is None:
        tickers = set()
        if not current.empty and "ticker" in current.columns:
            tickers |= set(current["ticker"])
        if not target.empty and "ticker" in target.columns:
            tickers |= set(target["ticker"])
        prices = _prices_for(*tickers)
    if config is None:
        config = _CONFIG
    return generate_trades(
        current, target, prices, config,
        conn=conn, score_date=score_date, commit=False,
    )


# ---------------------------------------------------------------------------
# Action classification
# ---------------------------------------------------------------------------

class TestActionClassification:
    def test_new_position_is_buy(self, tmp_db):
        current = _empty_current()
        target  = _target_df([{"ticker": "AAPL", "shares": 100}])
        trades  = _run(current, target, conn=tmp_db)
        row = trades[trades["ticker"] == "AAPL"].iloc[0]
        assert row["action"] == "BUY"

    def test_closed_long_is_sell(self, tmp_db):
        current = _current_df([("AAPL", 100)])
        target  = _empty_current()
        trades  = _run(current, target, conn=tmp_db)
        row = trades[trades["ticker"] == "AAPL"].iloc[0]
        assert row["action"] == "SELL"

    def test_new_short_is_short(self, tmp_db):
        current = _empty_current()
        target  = _target_df([{"ticker": "MSFT", "shares": -50, "direction": "SHORT"}])
        trades  = _run(current, target, conn=tmp_db)
        row = trades[trades["ticker"] == "MSFT"].iloc[0]
        assert row["action"] == "SHORT"

    def test_covered_short_is_cover(self, tmp_db):
        """Reducing a short (current=-50, target=-10) → delta=+40 → COVER."""
        current = _current_df([("MSFT", -50)])
        target  = _target_df([{"ticker": "MSFT", "shares": -10, "direction": "SHORT"}])
        trades  = _run(current, target, conn=tmp_db)
        row = trades[trades["ticker"] == "MSFT"].iloc[0]
        assert row["action"] == "COVER"

    def test_hold_when_delta_below_threshold(self, tmp_db):
        """Delta of 0.5 shares is below 1-share threshold → HOLD."""
        current = _current_df([("AAPL", 100.0)])
        target  = _target_df([{"ticker": "AAPL", "shares": 100.5}])
        trades  = _run(current, target, conn=tmp_db)
        row = trades[trades["ticker"] == "AAPL"].iloc[0]
        assert row["action"] == "HOLD"


# ---------------------------------------------------------------------------
# Turnover budget
# ---------------------------------------------------------------------------

class TestTurnoverBudget:
    def test_turnover_budget_trims_smallest_score_changes(self, tmp_db):
        """10 new positions each worth 5% of NAV=1M → 50k each.
        Budget=20% → 200k total → ~4 positions can trade (closures excluded).
        Remaining 6 should be HOLD."""
        tickers = [f"T{i}" for i in range(10)]
        current = _empty_current()
        target_rows = [
            {
                "ticker":        t,
                "shares":        500,       # 500 shares × $100 = $50k = 5% of $1M NAV
                "combined_score": float(50 + i * 3),
            }
            for i, t in enumerate(tickers)
        ]
        target  = _target_df(target_rows)
        prices  = _prices_for(*tickers)
        trades  = _run(current, target, prices=prices, conn=tmp_db)

        active  = trades[trades["action"] != "HOLD"]
        held    = trades[trades["action"] == "HOLD"]
        # Some trades must be trimmed to HOLD
        assert len(held) > 0
        # Total trade value of active trades ≤ budget (20% of 1M = 200k)
        trade_value = (active["delta_shares"].abs() * 100.0).sum()
        assert trade_value <= 200_000 * 1.05  # small tolerance

    def test_closures_are_never_trimmed(self, tmp_db):
        """Full closures (target_shares=0) must not be downgraded to HOLD."""
        # Start with 3 positions, close all
        current = _current_df([("A", 5000), ("B", 5000), ("C", 5000)])
        target  = _empty_current()
        prices  = _prices_for("A", "B", "C")
        trades  = _run(current, target, prices=prices, conn=tmp_db)
        actions = set(trades[trades["ticker"].isin(["A", "B", "C"])]["action"])
        assert "HOLD" not in actions

    def test_budget_split_preserves_both_books(self, tmp_db):
        """Budget exhausted by LONGs alone must still allow high-conviction SHORTs.

        6 LONGs with score=100 each cost $50k → $300k total = 30% NAV.
        6 SHORTs with score=1   each cost $50k → $300k total.
        Budget=20% of $1M = $200k. Without book-splitting, LONGs fill budget first.
        With book splitting: $100k per book → ~2 trades each side.
        """
        long_tickers  = [f"L{i}" for i in range(6)]
        short_tickers = [f"S{i}" for i in range(6)]
        all_tickers   = long_tickers + short_tickers

        current = _empty_current()
        target_rows = (
            [{"ticker": t, "shares":  500, "direction": "LONG",  "combined_score": 100.0}
             for t in long_tickers] +
            [{"ticker": t, "shares": -500, "direction": "SHORT", "combined_score":   1.0}
             for t in short_tickers]
        )
        target = _target_df(target_rows)
        prices = _prices_for(*all_tickers)
        trades = _run(current, target, prices=prices, conn=tmp_db)

        active_longs  = trades[(trades["action"] != "HOLD") & (trades["direction"] == "LONG")]
        active_shorts = trades[(trades["action"] != "HOLD") & (trades["direction"] == "SHORT")]
        assert len(active_shorts) > 0, "SHORTs were starved by LONG book"
        assert len(active_longs)  > 0, "LONGs should also appear"


# ---------------------------------------------------------------------------
# Priority column
# ---------------------------------------------------------------------------

class TestPriorityColumn:
    def test_priority_column_is_sequential(self, tmp_db):
        current = _current_df([("AAPL", 100), ("MSFT", -50)])
        target  = _target_df([
            {"ticker": "AAPL", "shares": 150},
            {"ticker": "MSFT", "shares": -80, "direction": "SHORT"},
            {"ticker": "GOOG", "shares": 200},
        ])
        prices = _prices_for("AAPL", "MSFT", "GOOG")
        trades = _run(current, target, prices=prices, conn=tmp_db)
        prio = sorted(trades["priority"].tolist())
        assert prio == list(range(1, len(trades) + 1))
