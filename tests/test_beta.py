"""Tests for portfolio/beta.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

import portfolio.db  # noqa: F401
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
        rows.append(
            {
                "ticker": ticker,
                "date": str(d + timedelta(days=i)),
                "adj_close": float(p),
                "open": float(p),
                "high": float(p) * 1.01,
                "low": float(p) * 0.99,
                "close": float(p),
                "volume": 10000,
            }
        )
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
        # Raw beta 0, shrunk towards 1 (packet 52): 0.67 * 0 + 0.33
        assert betas["FLAT"] == pytest.approx(0.33, abs=0.05)

    def test_missing_ticker_defaults_to_one(self, tmp_db):
        """Ticker not in DB → beta = 1.0."""
        spy_prices = _monotone_prices(n=30, start=100.0, step=1.0)
        _insert_prices(tmp_db, "SPY", spy_prices)
        betas = compute_betas(tmp_db, ["NOTEXIST"], "2026-01-31", lookback_days=60)
        assert betas["NOTEXIST"] == pytest.approx(1.0)

    def test_insufficient_history_defaults_to_one(self, tmp_db):
        """Only 5 price rows for ticker → below the 20-return threshold → beta = 1.0."""
        spy_prices = _monotone_prices(n=30, start=100.0, step=1.0)
        _insert_prices(tmp_db, "SPY", spy_prices)
        short_prices = _monotone_prices(n=5, start=50.0, step=0.5)
        _insert_prices(tmp_db, "SHORT", short_prices)
        betas = compute_betas(tmp_db, ["SHORT"], "2026-01-31", lookback_days=60)
        assert betas["SHORT"] == pytest.approx(1.0)

    def test_portfolio_beta_weighted_sum(self):
        weights = pd.Series({"A": 0.5, "B": -0.3})
        betas = pd.Series({"A": 1.2, "B": 0.8})
        result = portfolio_beta(weights, betas)
        expected = 0.5 * 1.2 + (-0.3) * 0.8
        assert result == pytest.approx(expected, abs=0.02)

    def test_portfolio_beta_empty_returns_zero(self):
        weights = pd.Series(dtype=float)
        betas = pd.Series(dtype=float)
        result = portfolio_beta(weights, betas)
        assert result == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Packet 52: pairwise betas and Blume shrinkage
# ---------------------------------------------------------------------------


def _random_walk(n, seed, scale=0.01, start=100.0, base=None, beta=1.0):
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, scale, n)
    rets = noise if base is None else beta * base + noise * 0.3
    return start * np.exp(np.cumsum(rets)), rets


def _spy_and(tmp_db, n=60, beta=2.0):
    spy_prices, spy_rets = _random_walk(n, seed=1)
    _insert_prices(tmp_db, "SPY", spy_prices)
    a_prices, _ = _random_walk(n, seed=2, base=spy_rets, beta=beta)
    _insert_prices(tmp_db, "AAA", a_prices)
    return spy_rets


class TestPairwise:
    def test_beta_same_alone_or_with_gappy_peer(self, tmp_db):
        # H3: a peer missing most days used to drop those days for every ticker.
        _spy_and(tmp_db)
        gappy = [100.0 + i for i in range(60)]
        d = date.fromisoformat("2026-01-01")
        rows = [
            {"ticker": "GAP", "date": str(d + timedelta(days=i)), "adj_close": p, "close": p}
            for i, p in enumerate(gappy)
            if i % 3 == 0
        ]
        tmp_db.execute(daily_prices.insert(), rows)
        tmp_db.commit()
        alone = compute_betas(tmp_db, ["AAA"], "2026-03-31")["AAA"]
        together = compute_betas(tmp_db, ["AAA", "GAP"], "2026-03-31")["AAA"]
        assert together == pytest.approx(alone)
        assert alone != pytest.approx(1.0, abs=0.05)  # a real estimate, not the default

    def test_lookback_counts_spy_days(self, tmp_db):
        # The window is the last N SPY returns: a shorter lookback gives a different estimate.
        _spy_and(tmp_db, n=60)
        full = compute_betas(tmp_db, ["AAA"], "2026-03-31", lookback_days=59)["AAA"]
        short = compute_betas(tmp_db, ["AAA"], "2026-03-31", lookback_days=20)["AAA"]
        assert full != pytest.approx(short)

    def test_peer_with_off_calendar_date(self, tmp_db):
        # Adversarial: a peer row on a day SPY lacks must not break SPY's next return.
        _spy_and(tmp_db)
        tmp_db.execute(
            daily_prices.insert().values(
                ticker="ODD", date="2026-01-10T12", adj_close=10.0, close=10.0
            )
        )
        tmp_db.commit()
        alone = compute_betas(tmp_db, ["AAA"], "2026-03-31")["AAA"]
        together = compute_betas(tmp_db, ["AAA", "ODD"], "2026-03-31")["AAA"]
        assert together == pytest.approx(alone)


class TestShrinkage:
    def test_blume_adjustment(self, tmp_db):
        spy_rets = _spy_and(tmp_db, beta=2.0)
        raw = pd.Series(np.diff(np.log(_random_walk(60, seed=2, base=spy_rets, beta=2.0)[0])))
        spy = pd.Series(np.diff(np.log(_random_walk(60, seed=1)[0])))
        expected_raw = raw.cov(spy) / spy.var()
        beta = compute_betas(tmp_db, ["AAA"], "2026-03-31")["AAA"]
        assert beta == pytest.approx(0.67 * expected_raw + 0.33, rel=1e-6)

    def test_extreme_beta_pulled_in(self, tmp_db):
        _spy_and(tmp_db, beta=4.0)
        assert compute_betas(tmp_db, ["AAA"], "2026-03-31")["AAA"] < 3.5

    def test_negative_beta_shrunk_towards_one(self, tmp_db):
        # Adversarial: shrinkage moves a negative beta up, not further away.
        _spy_and(tmp_db, beta=-0.5)
        beta = compute_betas(tmp_db, ["AAA"], "2026-03-31")["AAA"]
        assert -0.5 < beta < 0.33

    def test_fewer_than_twenty_shared_returns_defaults(self, tmp_db):
        spy_prices, _ = _random_walk(60, seed=1)
        _insert_prices(tmp_db, "SPY", spy_prices)
        _insert_prices(tmp_db, "NEW", [50.0 + i for i in range(15)], start_date="2026-02-15")
        assert compute_betas(tmp_db, ["NEW"], "2026-03-31")["NEW"] == pytest.approx(1.0)
