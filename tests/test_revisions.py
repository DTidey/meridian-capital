"""Tests for factors/revisions.py."""

import sys
from pathlib import Path
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from factors.revisions import compute, COLS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _estimates(ticker, n_days=90, eps_start=5.0, eps_growth=0.01):
    dates = pd.date_range("2024-01-01", periods=n_days, freq="D")
    eps   = [eps_start + eps_growth * i for i in range(n_days)]
    return pd.DataFrame({"ticker": ticker, "date": dates, "eps_estimate_fwd": eps,
                         "price_target": 120.0, "num_analysts": 10})


def _make_data(tickers, est_frames=None):
    universe = pd.DataFrame([(t, "IT") for t in tickers], columns=["ticker", "sector"])
    if est_frames is None:
        est_frames = {t: _estimates(t) for t in tickers}
    estimates = pd.concat(list(est_frames.values()), ignore_index=True) if est_frames else pd.DataFrame(
        columns=["ticker", "date", "eps_estimate_fwd", "price_target", "num_analysts"]
    )
    return {"universe": universe, "estimates": estimates}


_CONFIG = {"scoring": {"min_sector_size": 2}}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDegenerateMode:
    def test_no_history_yields_50(self):
        data = _make_data(["AAPL"], est_frames={})
        result = compute(data, _CONFIG)
        for col in COLS + ["revisions_score"]:
            assert result.loc["AAPL", col] == pytest.approx(50.0)

    def test_short_history_yields_50(self):
        # Only 10 days of data — below 30-day threshold
        data = _make_data(["AAPL"], est_frames={"AAPL": _estimates("AAPL", n_days=10)})
        result = compute(data, _CONFIG)
        for col in COLS:
            assert result.loc["AAPL", col] == pytest.approx(50.0)

    def test_empty_estimates_returns_all_50(self):
        data = _make_data(["AAPL", "MSFT"], est_frames={})
        result = compute(data, _CONFIG)
        assert (result == 50.0).all().all()


class TestRevisionDeltas:
    def test_positive_revision_scores_higher_than_negative(self):
        # UPWARD: eps rising over 90 days
        # DOWNWARD: eps falling over 90 days
        data = _make_data(
            ["UP", "DOWN"],
            est_frames={
                "UP":   _estimates("UP",   eps_start=5.0, eps_growth=0.05),
                "DOWN": _estimates("DOWN", eps_start=5.0, eps_growth=-0.05),
            },
        )
        result = compute(data, _CONFIG)
        assert result.loc["UP", "rev_30d"] > result.loc["DOWN", "rev_30d"]
        assert result.loc["UP", "rev_60d"] > result.loc["DOWN", "rev_60d"]

    def test_30d_delta_uses_30_day_lookback(self):
        # Flat for first 60 days, then big jump in last 30
        dates = pd.date_range("2024-01-01", periods=91, freq="D")
        eps   = [5.0] * 61 + [6.0] * 30
        est   = pd.DataFrame({"ticker": "JUMP", "date": dates, "eps_estimate_fwd": eps,
                               "price_target": 120.0, "num_analysts": 5})
        data  = _make_data(["JUMP", "FLAT"], est_frames={
            "JUMP": est,
            "FLAT": _estimates("FLAT", eps_start=5.0, eps_growth=0.0),
        })
        result = compute(data, _CONFIG)
        # JUMP should have much higher 30d revision than FLAT
        assert result.loc["JUMP", "rev_30d"] > result.loc["FLAT", "rev_30d"]


class TestStructure:
    def test_columns_present(self):
        data = _make_data(["AAPL", "MSFT"])
        result = compute(data, _CONFIG)
        assert set(COLS + ["revisions_score"]).issubset(result.columns)

    def test_scores_0_to_100(self):
        data = _make_data(["AAPL", "MSFT", "GOOG"])
        result = compute(data, _CONFIG)
        for col in COLS + ["revisions_score"]:
            assert result[col].between(0, 100).all(), f"{col} out of range"

    def test_equal_revisions_both_get_50(self):
        data = _make_data(["A", "B"], est_frames={
            "A": _estimates("A", eps_start=5.0, eps_growth=0.01),
            "B": _estimates("B", eps_start=5.0, eps_growth=0.01),
        })
        result = compute(data, _CONFIG)
        # Identical revision paths → both rank at 50
        for col in COLS:
            assert result.loc["A", col] == pytest.approx(result.loc["B", col])
