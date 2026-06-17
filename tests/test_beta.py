"""Tests for portfolio/beta.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import portfolio.db  # noqa: F401

from datetime import date, timedelta

import pandas as pd
import pytest

from data.db import daily_prices
from portfolio.beta import compute_betas, portfolio_beta


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _insert_prices(conn, ticker, prices, start_date="2026-01-01"):
    """Insert a list of adj_close prices into daily_prices starting from start_date."""
    d = date.fromisoformat(start_date)
    rows = []
    for i, p in enumerate(prices):
        rows.append({
            "ticker":    ticker,
            "date":      str(d + timedelta(days=i)),
            "adj_close": float(p),
            "open":      float(p),
            "high":      float(p) * 1.01,
            "low":       float(p) * 0.99,
            "close":     float(p),
            "volume":    10000,
        })
    conn.execute(daily_prices.insert(), rows)
    conn.commit()


def _monotone_prices(n=30, start=100.0, step=1.0):
    """Generate n prices going up by step each day."""
    return [start + i * step for i in range(n)]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestComputeBetas:
    def test_spy_beta_is_one(self, tmp_db):
        """SPY regressed against itself must give beta ≈ 1.0."""
        prices = _monotone_prices(n=30, start=100.0, step=0.5)
        _insert_prices(tmp_db, "SPY", prices)
        betas = compute_betas(tmp_db, ["SPY"], "2026-01-31", lookback_days=60)
        assert betas["SPY"] == pytest.approx(1.0, abs=0.1)

    def test_zero_correlation_returns_near_zero(self, tmp_db):
        """A ticker with constant price (zero return variance) → beta ≈ 0."""
        spy_prices = _monotone_prices(n=30, start=100.0, step=1.0)
        _insert_prices(tmp_db, "SPY", spy_prices)
        # Constant price → zero log-returns → zero covariance
        flat_prices = [50.0] * 30
        _insert_prices(tmp_db, "FLAT", flat_prices)
        betas = compute_betas(tmp_db, ["FLAT"], "2026-01-31", lookback_days=60)
        assert abs(betas["FLAT"]) == pytest.approx(0.0, abs=0.1)

    def test_missing_ticker_defaults_to_one(self, tmp_db):
        """Ticker not in DB → beta = 1.0."""
        spy_prices = _monotone_prices(n=30, start=100.0, step=1.0)
        _insert_prices(tmp_db, "SPY", spy_prices)
        betas = compute_betas(tmp_db, ["NOTEXIST"], "2026-01-31", lookback_days=60)
        assert betas["NOTEXIST"] == pytest.approx(1.0)

    def test_insufficient_history_defaults_to_one(self, tmp_db):
        """Only 5 price rows for ticker → below 10-row threshold → beta = 1.0."""
        spy_prices = _monotone_prices(n=30, start=100.0, step=1.0)
        _insert_prices(tmp_db, "SPY", spy_prices)
        short_prices = _monotone_prices(n=5, start=50.0, step=0.5)
        _insert_prices(tmp_db, "SHORT", short_prices)
        betas = compute_betas(tmp_db, ["SHORT"], "2026-01-31", lookback_days=60)
        assert betas["SHORT"] == pytest.approx(1.0)

    def test_portfolio_beta_weighted_sum(self):
        weights = pd.Series({"A": 0.5, "B": -0.3})
        betas   = pd.Series({"A": 1.2, "B": 0.8})
        result  = portfolio_beta(weights, betas)
        expected = 0.5 * 1.2 + (-0.3) * 0.8
        assert result == pytest.approx(expected, abs=0.02)

    def test_portfolio_beta_empty_returns_zero(self):
        weights = pd.Series(dtype=float)
        betas   = pd.Series(dtype=float)
        result  = portfolio_beta(weights, betas)
        assert result == pytest.approx(0.0)
