"""Tests for risk/correlation_monitor.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
import sqlalchemy as sa

import analysis.db  # noqa: F401
import factors.db  # noqa: F401
import portfolio.db  # noqa: F401
import risk.db  # noqa: F401
from data.db import daily_prices, initialise_schema
from risk.correlation_monitor import run_correlation_monitor

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCORE_DATE = "2026-04-01"


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


def _base_config(alert_avg_corr=0.60, lookback_days=60):
    return {
        "risk": {
            "correlation_monitor": {
                "alert_avg_corr": alert_avg_corr,
                "lookback_days": lookback_days,
            }
        }
    }


def _insert_prices_from_returns(conn, ticker, returns: np.ndarray, start="2026-01-01"):
    """Insert price series derived from a daily return array."""
    d = date.fromisoformat(start)
    price = 100.0
    rows = []
    # Insert one extra row as the base price so we get len(returns) log-return obs
    rows.append(
        {
            "ticker": ticker,
            "date": str(d - timedelta(days=1)),
            "adj_close": price,
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": 100_000,
        }
    )
    for i, r in enumerate(returns):
        price = price * np.exp(r)
        rows.append(
            {
                "ticker": ticker,
                "date": str(d + timedelta(days=i)),
                "adj_close": round(price, 6),
                "open": round(price, 6),
                "high": round(price, 6),
                "low": round(price, 6),
                "close": round(price, 6),
                "volume": 100_000,
            }
        )
    conn.execute(daily_prices.insert(), rows)
    conn.commit()


def _make_positions(tickers, direction="LONG"):
    return pd.DataFrame(
        {
            "ticker": tickers,
            "direction": [direction] * len(tickers),
            "weight": [0.10] * len(tickers),
        }
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEmptyPositions:
    def test_empty_positions_zero_corr(self, mem_db, tmp_path):
        """No positions → returns 0.0 for corr metrics."""
        result = run_correlation_monitor(
            mem_db, pd.DataFrame(), _SCORE_DATE, _base_config(), whatif=True
        )
        assert result["long_avg_corr"] == pytest.approx(0.0)
        assert result["short_avg_corr"] == pytest.approx(0.0)
        assert result["effective_n_bets"] == pytest.approx(0.0)
        assert result["alerts"] == []


class TestHighCorrelationAlert:
    def test_high_correlation_alert(self, mem_db, tmp_path):
        """Two LONG positions with near-identical returns (corr≈0.95) → alert fires."""
        rng = np.random.default_rng(42)
        base_ret = rng.normal(0, 0.01, size=60)
        noise = rng.normal(0, 0.001, size=60)  # tiny noise → very high corr

        _insert_prices_from_returns(mem_db, "AAPL", base_ret)
        _insert_prices_from_returns(mem_db, "MSFT", base_ret + noise)

        positions = _make_positions(["AAPL", "MSFT"], direction="LONG")
        result = run_correlation_monitor(
            mem_db, positions, _SCORE_DATE, _base_config(alert_avg_corr=0.60), whatif=True
        )
        assert result["long_avg_corr"] > 0.60
        assert len(result["alerts"]) >= 1
        assert result["alerts"][0]["book"] == "LONG"


class TestEffectiveNBetsIndependent:
    def test_effective_n_bets_independent(self, mem_db, tmp_path):
        """Two uncorrelated positions → effective_n_bets ≈ 2.0 (within 0.3)."""
        rng = np.random.default_rng(7)
        ret_a = rng.normal(0, 0.01, size=60)
        # Orthogonalise: subtract projection of ret_a onto ret_b direction
        ret_b_raw = rng.normal(0, 0.01, size=60)
        ret_b = ret_b_raw - np.dot(ret_b_raw, ret_a) / (np.dot(ret_a, ret_a) + 1e-12) * ret_a

        _insert_prices_from_returns(mem_db, "AAPL", ret_a)
        _insert_prices_from_returns(mem_db, "MSFT", ret_b)

        positions = _make_positions(["AAPL", "MSFT"], direction="LONG")
        result = run_correlation_monitor(
            mem_db, positions, _SCORE_DATE, _base_config(), whatif=True
        )
        assert result["effective_n_bets"] == pytest.approx(2.0, abs=0.5)


class TestEffectiveNBetsCorrelated:
    def test_effective_n_bets_correlated(self, mem_db, tmp_path):
        """Two highly correlated positions → effective_n_bets < 1.5."""
        rng = np.random.default_rng(13)
        base = rng.normal(0, 0.01, size=60)
        tiny = rng.normal(0, 0.0001, size=60)

        _insert_prices_from_returns(mem_db, "AAPL", base)
        _insert_prices_from_returns(mem_db, "MSFT", base + tiny)

        positions = _make_positions(["AAPL", "MSFT"], direction="LONG")
        result = run_correlation_monitor(
            mem_db, positions, _SCORE_DATE, _base_config(), whatif=True
        )
        assert result["effective_n_bets"] < 1.5


class TestBelowThresholdNoAlert:
    def test_below_threshold_no_alert(self, mem_db, tmp_path):
        """avg correlation 0.45 → no alerts when threshold is 0.60."""
        rng = np.random.default_rng(99)
        # Corr ≈ 0.45: mix 45% common factor, 55% idiosyncratic
        common = rng.normal(0, 0.01, size=60)
        idio_a = rng.normal(0, 0.01, size=60)
        idio_b = rng.normal(0, 0.01, size=60)
        ret_a = 0.45 * common + 0.55 * idio_a
        ret_b = 0.45 * common + 0.55 * idio_b

        _insert_prices_from_returns(mem_db, "AAPL", ret_a)
        _insert_prices_from_returns(mem_db, "MSFT", ret_b)

        positions = _make_positions(["AAPL", "MSFT"], direction="LONG")
        result = run_correlation_monitor(
            mem_db, positions, _SCORE_DATE, _base_config(alert_avg_corr=0.60), whatif=True
        )
        assert result["alerts"] == []
