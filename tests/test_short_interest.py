"""Tests for factors/short_interest.py."""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from factors.short_interest import COLS, compute

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _si_rows(ticker, n=40, base_pct=0.05, trend=0.0, base_ratio=None):
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    if base_ratio is None:
        base_ratio = base_pct * 40  # default: ratio scales with pct
    return pd.DataFrame(
        {
            "ticker": ticker,
            "date": dates,
            "short_pct_float": [base_pct + trend * i for i in range(n)],
            "short_ratio": [base_ratio + trend * i * 10 for i in range(n)],
            "shares_short": 1e6,
        }
    )


def _make_data(tickers, si_frames=None):
    universe = pd.DataFrame([(t, "IT") for t in tickers], columns=["ticker", "sector"])
    if si_frames is None:
        si_frames = {t: _si_rows(t) for t in tickers}
    si = (
        pd.concat(list(si_frames.values()), ignore_index=True)
        if si_frames
        else pd.DataFrame(
            columns=["ticker", "date", "short_pct_float", "short_ratio", "shares_short"]
        )
    )
    return {"universe": universe, "short_interest": si}


_CONFIG = {"scoring": {"min_sector_size": 2}}


# ---------------------------------------------------------------------------
# LONG convention: lower short interest = higher score
# ---------------------------------------------------------------------------


class TestLongConvention:
    def test_lower_short_pct_scores_higher(self):
        data = _make_data(
            ["LOW_SI", "HIGH_SI"],
            si_frames={
                "LOW_SI": _si_rows("LOW_SI", base_pct=0.02),
                "HIGH_SI": _si_rows("HIGH_SI", base_pct=0.20),
            },
        )
        result = compute(data, _CONFIG)
        assert result.loc["LOW_SI", "si_pct_float"] > result.loc["HIGH_SI", "si_pct_float"]

    def test_declining_short_interest_scores_higher(self):
        data = _make_data(
            ["DECLINING", "RISING"],
            si_frames={
                "DECLINING": _si_rows("DECLINING", trend=-0.001),
                "RISING": _si_rows("RISING", trend=+0.001),
            },
        )
        result = compute(data, _CONFIG)
        assert result.loc["DECLINING", "si_change"] > result.loc["RISING", "si_change"]

    def test_lower_days_to_cover_scores_higher(self):
        data = _make_data(
            ["EASY", "HARD"],
            si_frames={
                "EASY": _si_rows("EASY", base_pct=0.02),  # short_ratio=2
                "HARD": _si_rows("HARD", base_pct=0.20),  # short_ratio=20
            },
        )
        result = compute(data, _CONFIG)
        assert result.loc["EASY", "si_days_to_cover"] > result.loc["HARD", "si_days_to_cover"]


# ---------------------------------------------------------------------------
# Missing data
# ---------------------------------------------------------------------------


class TestMissingData:
    def test_no_si_data_returns_all_50(self):
        data = _make_data(["AAPL"], si_frames={})
        result = compute(data, _CONFIG)
        assert (result == 50.0).all().all()

    def test_ticker_missing_from_si_gets_50(self):
        data = _make_data(["AAPL", "MSFT"], si_frames={"AAPL": _si_rows("AAPL")})
        result = compute(data, _CONFIG)
        for col in COLS:
            assert result.loc["MSFT", col] == pytest.approx(50.0)

    def test_no_prior_30d_snapshot_si_change_is_50(self):
        # Only 5 days of data — can't compute 30-day change
        data = _make_data(
            ["AAPL", "MSFT"],
            si_frames={
                "AAPL": _si_rows("AAPL", n=5),
                "MSFT": _si_rows("MSFT", n=5),
            },
        )
        result = compute(data, _CONFIG)
        assert result.loc["AAPL", "si_change"] == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


class TestStructure:
    def test_columns_present(self):
        data = _make_data(["AAPL", "MSFT"])
        result = compute(data, _CONFIG)
        assert set(COLS + ["short_interest_score"]).issubset(result.columns)

    def test_scores_0_to_100(self):
        data = _make_data(["AAPL", "MSFT", "GOOG"])
        result = compute(data, _CONFIG)
        for col in COLS + ["short_interest_score"]:
            assert result[col].between(0, 100).all(), f"{col} out of range"

    def test_empty_universe(self):
        data = {
            "universe": pd.DataFrame(columns=["ticker", "sector"]),
            "short_interest": pd.DataFrame(),
        }
        result = compute(data, _CONFIG)
        assert len(result) == 0
