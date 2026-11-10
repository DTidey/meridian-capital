"""Tests for factors/regime_weights.py."""

import logging
import sys
from itertools import pairwise
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from factors.regime_weights import (
    _normalise,
    adjust_weights,
    blend_weights,
    regime_label,
    regime_shares,
    resolve,
    smoothed_vix,
    validate,
)

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
    "vix_smoothing_days": 5,
    "low_vol": {"vix_full": 12, "vix_none": 18, "momentum": 0.28, "value": 0.10},
    "high_vol": {"vix_none": 20, "vix_full": 30, "quality": 0.28, "value": 0.22, "momentum": 0.10},
}


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


# ---------------------------------------------------------------------------
# Packet 64: VIX-band blend on a trailing mean (AC6, AC7, AC10)
# ---------------------------------------------------------------------------


def _vix(closes: dict[str, float]) -> pd.DataFrame:
    return pd.DataFrame({"date": list(closes), "close": list(closes.values())})


# The 11-04 .. 11-09 episode: closes as stored in daily_prices (^VIX)
_EPISODE = _vix(
    {
        "2026-10-30": 19.42,
        "2026-11-02": 21.15,
        "2026-11-03": 20.83,
        "2026-11-04": 16.61,
        "2026-11-05": 28.18,
        "2026-11-06": 25.64,
        "2026-11-09": 19.92,
    }
)


def _approx(a: dict, b: dict) -> None:
    assert a.keys() == b.keys()
    for k in a:
        assert a[k] == pytest.approx(b[k], abs=1e-12), k


class TestBlend:
    def test_neutral_zone_base_weights(self):
        for vix in (18.0, 19.0, 20.0):
            high, low = regime_shares(vix, _REGIME_CFG)
            assert (high, low) == (0.0, 0.0)
            _approx(blend_weights(_BASE_WEIGHTS, _REGIME_CFG, high, low), _BASE_WEIGHTS)

    def test_full_edge_equals_regime_weights(self):
        for vix, regime in (
            (30.0, "HIGH_VOL"),
            (45.0, "HIGH_VOL"),
            (12.0, "LOW_VOL"),
            (9.0, "LOW_VOL"),
        ):
            high, low = regime_shares(vix, _REGIME_CFG)
            got = blend_weights(_BASE_WEIGHTS, _REGIME_CFG, high, low)
            _approx(got, adjust_weights(_BASE_WEIGHTS, regime, _REGIME_CFG))

    def test_midpoint_blend(self):
        high, low = regime_shares(25.0, _REGIME_CFG)
        assert (high, low) == (0.5, 0.0)
        got = blend_weights(_BASE_WEIGHTS, _REGIME_CFG, high, low)
        full = adjust_weights(_BASE_WEIGHTS, "HIGH_VOL", _REGIME_CFG)
        for k in _BASE_WEIGHTS:
            assert got[k] == pytest.approx(0.5 * _BASE_WEIGHTS[k] + 0.5 * full[k])

    def test_weights_sum_to_one(self):
        for vix in (5.0, 12.0, 13.7, 15.0, 17.99, 18.0, 22.48, 25.0, 29.9, 30.0, 80.0):
            got = blend_weights(_BASE_WEIGHTS, _REGIME_CFG, *regime_shares(vix, _REGIME_CFG))
            assert sum(got.values()) == pytest.approx(1.0)
            assert all(v >= 0 for v in got.values())

    def test_smoothed_vix_mean_of_last_n(self):
        got = smoothed_vix(_EPISODE, 5, "2026-11-09")
        assert got == pytest.approx((20.83 + 16.61 + 28.18 + 25.64 + 19.92) / 5)
        high, _ = regime_shares(got, _REGIME_CFG)
        assert high == pytest.approx(0.2236, abs=1e-4)

    def test_window_gap_uses_available_closes(self, caplog):
        vix = _vix({"2026-10-01": 40.0, "2026-11-05": 28.0, "2026-11-09": 20.0})
        with caplog.at_level(logging.WARNING, logger="factors.regime_weights"):
            got = smoothed_vix(vix, 5, "2026-11-09")
        assert got == pytest.approx(24.0)  # the 10-01 close is outside the 14-day window
        assert "2 of 5 closes" in caplog.text

    def test_single_close_with_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="factors.regime_weights"):
            got = smoothed_vix(_vix({"2026-11-09": 19.92}), 5, "2026-11-09")
        assert got == pytest.approx(19.92)
        assert "1 of 5 closes" in caplog.text

    def test_stateless_rerun(self):
        a = resolve(_EPISODE, _BASE_WEIGHTS, {"regime_weights": _REGIME_CFG}, "2026-11-06")
        shuffled = _EPISODE.sample(frac=1, random_state=3)
        b = resolve(shuffled, _BASE_WEIGHTS, {"regime_weights": _REGIME_CFG}, "2026-11-06")
        assert a == b

    def test_exact_edges(self):
        assert regime_shares(20.0, _REGIME_CFG) == (0.0, 0.0)
        assert regime_shares(30.0, _REGIME_CFG) == (1.0, 0.0)
        assert regime_shares(18.0, _REGIME_CFG) == (0.0, 0.0)
        assert regime_shares(12.0, _REGIME_CFG) == (0.0, 1.0)

    def test_future_vix_ignored(self):
        """No lookahead: closes dated after the score date never enter the mean."""
        got = smoothed_vix(_EPISODE, 5, "2026-11-04")
        expected = _EPISODE[_EPISODE["date"] <= "2026-11-04"]["close"].tail(5).mean()
        assert got == pytest.approx(expected)
        assert smoothed_vix(_EPISODE, 1, "2026-11-04") == pytest.approx(16.61)

    def test_episode_moves_gradually(self):
        """11-04 -> 11-09: no full weight jump; the label stays NORMAL throughout."""
        cfg = {"regime_weights": _REGIME_CFG}
        days = ["2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09"]
        states = [resolve(_EPISODE, _BASE_WEIGHTS, cfg, d) for d in days]
        highs = [s["high_vol_share"] for s in states]
        assert max(abs(b - a) for a, b in pairwise(highs)) < 0.2
        assert {s["regime"] for s in states} == {"NORMAL"}


class TestLabel:
    def test_label_thresholds(self):
        assert regime_label(0.5, 0.0) == "HIGH_VOL"
        assert regime_label(0.49, 0.0) == "NORMAL"
        assert regime_label(0.0, 0.5) == "LOW_VOL"
        assert regime_label(0.0, 0.499) == "NORMAL"
        assert regime_label(0.0, 0.0) == "NORMAL"

    def test_no_vix_normal_with_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="factors.regime_weights"):
            state = resolve(
                pd.DataFrame(), _BASE_WEIGHTS, {"regime_weights": _REGIME_CFG}, "2026-11-09"
            )
        assert state["regime"] == "NORMAL"
        assert (state["high_vol_share"], state["low_vol_share"]) == (0.0, 0.0)
        assert state["vix_close"] is None and state["vix_smoothed"] is None
        _approx(state["weights"], _BASE_WEIGHTS)
        assert "VIX data unavailable" in caplog.text

    def test_conditional_weights_off(self):
        cfg = {"regime_conditional_weights": False, "regime_weights": _REGIME_CFG}
        state = resolve(_vix({"2026-11-09": 45.0}), _BASE_WEIGHTS, cfg, "2026-11-09")
        _approx(state["weights"], _BASE_WEIGHTS)
        assert state["regime"] == "NORMAL"
        assert state["vix_close"] == pytest.approx(45.0)

    def test_full_high_vol_label_and_close(self):
        state = resolve(
            _vix({"2026-11-09": 31.0}), _BASE_WEIGHTS, {"regime_weights": _REGIME_CFG}, "2026-11-09"
        )
        assert state["regime"] == "HIGH_VOL" and state["high_vol_share"] == 1.0


class TestRegimeConfig:
    def test_band_order_validated(self):
        bad = [
            {"low_vol": {"vix_full": 18, "vix_none": 12}},  # low band reversed
            {"low_vol": {"vix_none": 22}},  # low band overlaps the high band (none 20)
            {"high_vol": {"vix_none": 30, "vix_full": 20}},  # high band reversed
            {"high_vol": {"vix_full": 20}},  # empty high band
        ]
        for cfg in bad:
            with pytest.raises(ValueError, match="band edges"):
                validate(cfg)

    def test_touching_bands_allowed(self):
        validate({"low_vol": {"vix_full": 12, "vix_none": 20}, "high_vol": {"vix_none": 20}})

    @pytest.mark.parametrize("days", [0, -1, 2.5, "5", True])
    def test_smoothing_days_validated(self, days):
        with pytest.raises(ValueError, match="vix_smoothing_days"):
            validate({"vix_smoothing_days": days})

    def test_defaults_when_missing(self):
        validate({})
        assert regime_shares(25.0, {}) == (0.5, 0.0)

    def test_shipped_config_valid(self):
        import yaml

        cfg = yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text())
        rw = cfg["scoring"]["regime_weights"]
        validate(rw)
        assert "vix_below" not in rw["low_vol"] and "vix_above" not in rw["high_vol"]
