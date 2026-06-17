"""Tests for risk/stress_test.py — synthetic scenarios only (no yfinance calls)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import unittest.mock as mock

import pandas as pd
import pytest
import sqlalchemy as sa

import analysis.db  # noqa: F401
import factors.db  # noqa: F401
import portfolio.db  # noqa: F401
import risk.db  # noqa: F401
from data.db import initialise_schema
from risk.stress_test import (
    ScenarioResult,
    _run_synthetic,
    run_stress_tests,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCORE_DATE = "2026-04-01"
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


def _base_config(nav=_NAV):
    return {"portfolio": {"nav_usd": nav}}


def _make_positions(tickers, directions=None, weights=None):
    n = len(tickers)
    if directions is None:
        directions = ["LONG"] * n
    if weights is None:
        weights = [0.10] * n
    return pd.DataFrame(
        {
            "ticker": tickers,
            "direction": directions,
            "weight": weights,
            "sector": ["Technology"] * n,
        }
    )


def _make_factor_scores(tickers, sectors=None, mom_scores=None):
    n = len(tickers)
    if sectors is None:
        sectors = ["Technology"] * n
    if mom_scores is None:
        mom_scores = [50.0] * n
    return pd.DataFrame(
        {
            "ticker": tickers,
            "sector": sectors,
            "momentum_score": mom_scores,
        }
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestShortSqueeze:
    def test_short_squeeze_hurts_short_book(self, mem_db, tmp_path):
        """Short squeeze: short book P&L should be negative."""
        # Short positions: weight < 0, +30% return → pnl = weight * ret * nav < 0
        tickers = ["TSLA", "GME", "AMC"]
        positions = pd.DataFrame(
            {
                "ticker": tickers,
                "direction": ["SHORT", "SHORT", "SHORT"],
                "weight": [-0.05, -0.05, -0.05],
                "sector": ["Consumer Discretionary"] * 3,
            }
        )
        factor_scores = _make_factor_scores(tickers)

        result = _run_synthetic(
            positions_df=positions,
            scenario_name="short_squeeze",
            factor_scores_df=factor_scores,
            nav_usd=_NAV,
            score_date=_SCORE_DATE,
        )
        assert isinstance(result, ScenarioResult)
        assert result.short_pnl_usd < 0.0, (
            f"Short book P&L should be negative in a short squeeze (got {result.short_pnl_usd})"
        )


class TestSectorShock:
    def test_sector_shock_concentrates_on_top_sector(self, mem_db, tmp_path):
        """Largest sector takes -30%, others flat → only that sector loses money."""
        tickers = ["AAPL", "MSFT", "XOM", "CVX"]
        directions = ["LONG", "LONG", "LONG", "LONG"]
        weights = [0.20, 0.20, 0.05, 0.05]
        sectors = ["Technology", "Technology", "Energy", "Energy"]

        positions = pd.DataFrame(
            {
                "ticker": tickers,
                "direction": directions,
                "weight": weights,
                "sector": sectors,
            }
        )
        factor_scores = pd.DataFrame(
            {
                "ticker": tickers,
                "sector": sectors,
                "momentum_score": [50.0] * 4,
            }
        )

        result = _run_synthetic(
            positions_df=positions,
            scenario_name="sector_shock",
            factor_scores_df=factor_scores,
            nav_usd=_NAV,
            score_date=_SCORE_DATE,
        )
        # Technology has the highest gross exposure (0.40 vs 0.10 for Energy)
        # So Technology gets -30%; P&L = (0.20 + 0.20) * (-0.30) * 10M = -1,200,000
        assert result.total_pnl_usd == pytest.approx(-1_200_000.0, abs=1.0)


class TestMomentumReversal:
    def test_momentum_reversal_top_quintile_loses(self, mem_db, tmp_path):
        """Top momentum tickers lose, bottom momentum tickers gain."""
        tickers = ["A", "B", "C", "D", "E"]
        mom_scores = [90.0, 70.0, 50.0, 30.0, 10.0]  # A is top, E is bottom
        weights = [0.10] * 5

        positions = pd.DataFrame(
            {
                "ticker": tickers,
                "direction": ["LONG"] * 5,
                "weight": weights,
                "sector": ["Technology"] * 5,
            }
        )
        factor_scores = pd.DataFrame(
            {
                "ticker": tickers,
                "sector": ["Technology"] * 5,
                "momentum_score": mom_scores,
            }
        )

        result = _run_synthetic(
            positions_df=positions,
            scenario_name="momentum_reversal",
            factor_scores_df=factor_scores,
            nav_usd=_NAV,
            score_date=_SCORE_DATE,
        )
        # A (top quintile, score=90 >= p80) should lose: pnl = 0.10 * (-0.20) * 10M = -200k
        # E (bottom quintile, score=10 <= p20) should gain: pnl = 0.10 * (+0.20) * 10M = +200k
        assert result.total_pnl_usd == pytest.approx(0.0, abs=1.0)
        assert result.long_pnl_usd == pytest.approx(0.0, abs=1.0)


class TestEmptyPositionsZeroPnl:
    def test_empty_positions_zero_pnl(self, mem_db, tmp_path):
        """All scenarios return 0 P&L for empty portfolio (synthetic scenarios)."""
        empty_positions = pd.DataFrame(columns=["ticker", "direction", "weight", "sector"])
        factor_scores = pd.DataFrame(columns=["ticker", "sector", "momentum_score"])

        for scenario in ["sector_shock", "momentum_reversal", "short_squeeze"]:
            result = _run_synthetic(
                positions_df=empty_positions,
                scenario_name=scenario,
                factor_scores_df=factor_scores,
                nav_usd=_NAV,
                score_date=_SCORE_DATE,
            )
            assert result.total_pnl_usd == pytest.approx(0.0), (
                f"scenario={scenario} expected 0 P&L for empty portfolio"
            )
            assert result.total_pnl_pct == pytest.approx(0.0)


class TestScenarioFilter:
    def test_scenario_filter(self, mem_db, tmp_path):
        """Passing scenarios=['sector_shock'] only runs that scenario."""
        positions = _make_positions(["AAPL", "MSFT"], weights=[0.10, 0.10])

        # Patch _load_or_fetch_prices to avoid yfinance calls for historical scenarios
        with mock.patch("risk.stress_test._load_or_fetch_prices", return_value=None):
            results = run_stress_tests(
                conn=mem_db,
                positions_df=positions,
                score_date=_SCORE_DATE,
                config=_base_config(),
                cache_dir=tmp_path,
                scenarios=["sector_shock"],
            )

        assert len(results) == 1
        assert results[0].name == "sector_shock"
