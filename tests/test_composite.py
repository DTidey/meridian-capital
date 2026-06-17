"""Tests for factors/composite.py."""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from factors.composite import _validate_weights, compute

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_WEIGHTS = {
    "momentum": 0.20,
    "quality": 0.20,
    "value": 0.15,
    "revisions": 0.15,
    "insider": 0.10,
    "growth": 0.10,
    "short_interest": 0.05,
    "institutional": 0.05,
}

_CONFIG = {
    "scoring": {
        "long_quintile_threshold": 80,
        "short_quintile_threshold": 20,
        "min_sector_size": 2,
    }
}


def _make_universe(tickers, sector="IT"):
    return pd.DataFrame({"ticker": tickers, "sector": sector})


def _uniform_factor_scores(tickers, score=50.0):
    """All factors at the same score for all tickers."""
    factor_dfs = {}
    factor_cols = {
        "momentum": ["momentum_score", "mom_12_1", "mom_6m"],
        "quality": ["quality_score", "qual_piotroski"],
        "value": ["value_score", "val_fcf_yield"],
        "revisions": ["revisions_score", "rev_30d"],
        "insider": ["insider_score", "ins_net_flow"],
        "growth": ["growth_score", "grw_rev_yoy"],
        "short_interest": ["short_interest_score", "si_pct_float"],
        "institutional": ["institutional_score", "inst_funds_holding"],
    }
    for factor, cols in factor_cols.items():
        factor_dfs[factor] = pd.DataFrame(score, index=tickers, columns=cols)
    return factor_dfs


def _varied_factor_scores(tickers, scores_map):
    """scores_map: {ticker: composite_leaning_score}."""
    factor_dfs = {}
    factor_cols = {
        "momentum": "momentum_score",
        "quality": "quality_score",
        "value": "value_score",
        "revisions": "revisions_score",
        "insider": "insider_score",
        "growth": "growth_score",
        "short_interest": "short_interest_score",
        "institutional": "institutional_score",
    }
    for factor, score_col in factor_cols.items():
        df = pd.DataFrame({score_col: scores_map}, dtype=float)
        factor_dfs[factor] = df
    return factor_dfs


# ---------------------------------------------------------------------------
# Weight validation
# ---------------------------------------------------------------------------


class TestValidateWeights:
    def test_valid_weights_no_error(self):
        _validate_weights(_DEFAULT_WEIGHTS)  # should not raise

    def test_weights_not_summing_to_one_raises(self):
        bad = dict(_DEFAULT_WEIGHTS)
        bad["momentum"] = 0.50
        with pytest.raises(ValueError, match="sum to 1.0"):
            _validate_weights(bad)


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


class TestStructure:
    def test_composite_score_column_present(self):
        tickers = ["AAPL", "MSFT"]
        result = compute(
            _uniform_factor_scores(tickers),
            _make_universe(tickers),
            _DEFAULT_WEIGHTS,
            _CONFIG,
        )
        assert "composite_score" in result.columns

    def test_direction_column_present(self):
        tickers = ["AAPL", "MSFT"]
        result = compute(
            _uniform_factor_scores(tickers),
            _make_universe(tickers),
            _DEFAULT_WEIGHTS,
            _CONFIG,
        )
        assert "direction" in result.columns

    def test_all_tickers_present(self):
        tickers = ["AAPL", "MSFT", "GOOG"]
        result = compute(
            _uniform_factor_scores(tickers),
            _make_universe(tickers),
            _DEFAULT_WEIGHTS,
            _CONFIG,
        )
        assert set(result.index) == set(tickers)

    def test_composite_score_between_0_and_100(self):
        tickers = ["A", "B", "C", "D", "E"]
        scores = {"A": 90, "B": 70, "C": 50, "D": 30, "E": 10}
        result = compute(
            _varied_factor_scores(tickers, scores),
            _make_universe(tickers),
            _DEFAULT_WEIGHTS,
            _CONFIG,
        )
        assert result["composite_score"].between(0, 100).all()


# ---------------------------------------------------------------------------
# LONG / SHORT labelling
# ---------------------------------------------------------------------------


class TestDirectionLabels:
    def test_top_scorer_is_long(self):
        tickers = list("ABCDEFGHIJ")  # 10 tickers
        # A gets score 99, all others get 1
        scores = {t: (99 if t == "A" else 1) for t in tickers}
        result = compute(
            _varied_factor_scores(tickers, scores),
            _make_universe(tickers),
            _DEFAULT_WEIGHTS,
            _CONFIG,
        )
        assert result.loc["A", "direction"] == "LONG"

    def test_bottom_scorer_is_short(self):
        tickers = list("ABCDEFGHIJ")
        scores = {t: (1 if t == "J" else 99) for t in tickers}
        result = compute(
            _varied_factor_scores(tickers, scores),
            _make_universe(tickers),
            _DEFAULT_WEIGHTS,
            _CONFIG,
        )
        assert result.loc["J", "direction"] == "SHORT"

    def test_middle_scorer_is_neutral(self):
        tickers = list("ABCDE")
        scores = {"A": 90, "B": 70, "C": 50, "D": 30, "E": 10}
        result = compute(
            _varied_factor_scores(tickers, scores),
            _make_universe(tickers),
            _DEFAULT_WEIGHTS,
            _CONFIG,
        )
        assert result.loc["C", "direction"] == "NEUTRAL"

    def test_all_equal_scores_are_neutral(self):
        tickers = ["A", "B", "C"]
        result = compute(
            _uniform_factor_scores(tickers, 50.0),
            _make_universe(tickers),
            _DEFAULT_WEIGHTS,
            _CONFIG,
        )
        assert (result["direction"] == "NEUTRAL").all()


# ---------------------------------------------------------------------------
# Composite ordering
# ---------------------------------------------------------------------------


class TestCompositeOrdering:
    def test_higher_factor_scores_give_higher_composite(self):
        tickers = ["HIGH", "MED", "LOW"]
        scores = {"HIGH": 90, "MED": 50, "LOW": 10}
        result = compute(
            _varied_factor_scores(tickers, scores),
            _make_universe(tickers),
            _DEFAULT_WEIGHTS,
            _CONFIG,
        )
        assert result.loc["HIGH", "composite_score"] > result.loc["MED", "composite_score"]
        assert result.loc["MED", "composite_score"] > result.loc["LOW", "composite_score"]


# ---------------------------------------------------------------------------
# Missing factor
# ---------------------------------------------------------------------------


class TestMissingFactor:
    def test_missing_factor_substitutes_50(self):
        tickers = ["AAPL", "MSFT"]
        factor_scores = _uniform_factor_scores(tickers)
        del factor_scores["momentum"]  # remove one factor
        result = compute(factor_scores, _make_universe(tickers), _DEFAULT_WEIGHTS, _CONFIG)
        # Should not raise; momentum treated as 50
        assert "composite_score" in result.columns
