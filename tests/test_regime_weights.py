"""Tests for factors/regime_weights.py."""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from factors.regime_weights import _normalise, adjust_weights, resolve_regime

_BASE_WEIGHTS = {
    "momentum": 0.20,
    "quality": 0.20,
    "value": 0.15,
    "revisions": 0.15,
    "insider": 0.10,
    "growth": 0.10,
    "short_interest": 0.05,
    "institutional": 0.05,
}

_REGIME_CFG = {
    "low_vol": {"vix_below": 15, "momentum": 0.28, "value": 0.10},
    "high_vol": {"vix_above": 25, "quality": 0.28, "value": 0.22, "momentum": 0.10},
}


# ---------------------------------------------------------------------------
# resolve_regime
# ---------------------------------------------------------------------------


class TestResolveRegime:
    def test_vix_below_15_is_low_vol(self):
        vix = pd.DataFrame([{"date": "2024-06-01", "close": 12.0}])
        regime, val = resolve_regime(vix)
        assert regime == "LOW_VOL"
        assert val == pytest.approx(12.0)

    def test_vix_at_15_is_normal(self):
        vix = pd.DataFrame([{"date": "2024-06-01", "close": 15.0}])
        regime, val = resolve_regime(vix)
        assert regime == "NORMAL"

    def test_vix_between_15_and_25_is_normal(self):
        vix = pd.DataFrame([{"date": "2024-06-01", "close": 20.0}])
        regime, val = resolve_regime(vix)
        assert regime == "NORMAL"

    def test_vix_at_25_is_normal(self):
        vix = pd.DataFrame([{"date": "2024-06-01", "close": 25.0}])
        regime, val = resolve_regime(vix)
        assert regime == "NORMAL"

    def test_vix_above_25_is_high_vol(self):
        vix = pd.DataFrame([{"date": "2024-06-01", "close": 35.0}])
        regime, val = resolve_regime(vix)
        assert regime == "HIGH_VOL"
        assert val == pytest.approx(35.0)

    def test_empty_vix_defaults_to_normal(self):
        regime, val = resolve_regime(pd.DataFrame())
        assert regime == "NORMAL"
        assert val is None


# ---------------------------------------------------------------------------
# adjust_weights
# ---------------------------------------------------------------------------


class TestAdjustWeights:
    def test_normal_regime_returns_unchanged(self):
        weights = adjust_weights(_BASE_WEIGHTS, "NORMAL", _REGIME_CFG)
        assert weights == pytest.approx(_BASE_WEIGHTS)

    def test_low_vol_boosts_momentum(self):
        weights = adjust_weights(_BASE_WEIGHTS, "LOW_VOL", _REGIME_CFG)
        assert weights["momentum"] > _BASE_WEIGHTS["momentum"]

    def test_low_vol_cuts_value(self):
        weights = adjust_weights(_BASE_WEIGHTS, "LOW_VOL", _REGIME_CFG)
        assert weights["value"] < _BASE_WEIGHTS["value"]

    def test_high_vol_boosts_quality(self):
        weights = adjust_weights(_BASE_WEIGHTS, "HIGH_VOL", _REGIME_CFG)
        assert weights["quality"] > _BASE_WEIGHTS["quality"]

    def test_high_vol_boosts_value(self):
        weights = adjust_weights(_BASE_WEIGHTS, "HIGH_VOL", _REGIME_CFG)
        assert weights["value"] > _BASE_WEIGHTS["value"]

    def test_high_vol_cuts_momentum(self):
        weights = adjust_weights(_BASE_WEIGHTS, "HIGH_VOL", _REGIME_CFG)
        assert weights["momentum"] < _BASE_WEIGHTS["momentum"]

    def test_weights_always_sum_to_one(self):
        for regime in ("LOW_VOL", "NORMAL", "HIGH_VOL"):
            weights = adjust_weights(_BASE_WEIGHTS, regime, _REGIME_CFG)
            assert sum(weights.values()) == pytest.approx(1.0)

    def test_all_weights_non_negative(self):
        for regime in ("LOW_VOL", "NORMAL", "HIGH_VOL"):
            weights = adjust_weights(_BASE_WEIGHTS, regime, _REGIME_CFG)
            for k, v in weights.items():
                assert v >= 0, f"Negative weight for {k} in {regime}"


# ---------------------------------------------------------------------------
# _normalise
# ---------------------------------------------------------------------------


class TestNormalise:
    def test_sums_to_one(self):
        w = {"a": 2.0, "b": 3.0, "c": 5.0}
        n = _normalise(w)
        assert sum(n.values()) == pytest.approx(1.0)

    def test_ratios_preserved(self):
        w = {"a": 2.0, "b": 4.0}
        n = _normalise(w)
        assert n["b"] / n["a"] == pytest.approx(2.0)

    def test_zero_total_raises(self):
        with pytest.raises(ValueError):
            _normalise({"a": 0.0, "b": 0.0})
