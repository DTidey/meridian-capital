"""Tests for factors/momentum.py."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from factors.momentum import COLS, _ret, compute

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_prices(ticker, n_days=300, start_price=100.0, growth=0.001):
    """Generate n_days of synthetic adj_close prices."""
    dates = pd.date_range("2023-01-01", periods=n_days, freq="B")
    prices = [start_price * (1 + growth) ** i for i in range(n_days)]
    return pd.DataFrame(
        {"ticker": ticker, "date": dates, "adj_close": prices, "close": prices, "volume": 1000}
    )


def _make_universe(tickers_sectors):
    return pd.DataFrame(tickers_sectors, columns=["ticker", "sector"])


def _make_data(tickers_sectors, price_kwargs=None):
    price_kwargs = price_kwargs or {}
    dfs = []
    for ticker, _ in tickers_sectors:
        kw = price_kwargs.get(ticker, {})
        dfs.append(_make_prices(ticker, **kw))
    return {
        "prices": pd.concat(dfs, ignore_index=True),
        "universe": _make_universe(tickers_sectors),
    }


_CONFIG = {
    "scoring": {
        "min_sector_size": 2,
        "sector_etf_map": {"Information Technology": "XLK"},
    }
}


# ---------------------------------------------------------------------------
# _ret helper
# ---------------------------------------------------------------------------


class TestRet:
    def test_positive_return(self):
        assert _ret(100.0, 110.0) == pytest.approx(0.10)

    def test_negative_return(self):
        assert _ret(100.0, 90.0) == pytest.approx(-0.10)

    def test_zero_start_returns_nan(self):
        assert np.isnan(_ret(0.0, 110.0))


# ---------------------------------------------------------------------------
# compute() — basic structure
# ---------------------------------------------------------------------------


class TestComputeStructure:
    def test_returns_dataframe_with_expected_columns(self):
        data = _make_data([("AAPL", "Information Technology"), ("MSFT", "Information Technology")])
        result = compute(data, _CONFIG)
        assert set(COLS + ["momentum_score"]).issubset(result.columns)

    def test_index_is_tickers(self):
        data = _make_data([("AAPL", "Information Technology"), ("MSFT", "Information Technology")])
        result = compute(data, _CONFIG)
        assert set(result.index) == {"AAPL", "MSFT"}

    def test_scores_between_0_and_100(self):
        tickers = [("AAPL", "IT"), ("MSFT", "IT"), ("GOOG", "IT")]
        data = _make_data(tickers)
        result = compute(data, _CONFIG)
        for col in COLS + ["momentum_score"]:
            assert result[col].between(0, 100).all(), f"{col} out of range"

    def test_empty_prices_returns_50_defaults(self):
        data = {
            "prices": pd.DataFrame(columns=["ticker", "date", "adj_close", "close", "volume"]),
            "universe": _make_universe([("AAPL", "IT")]),
        }
        result = compute(data, _CONFIG)
        assert (result == 50.0).all().all()


# ---------------------------------------------------------------------------
# Insufficient history
# ---------------------------------------------------------------------------


class TestInsufficientHistory:
    def test_ticker_with_few_days_gets_50(self):
        # AAPL has only 100 days — below _MIN_HISTORY=252
        data = _make_data(
            [("AAPL", "IT"), ("MSFT", "IT")],
            price_kwargs={"AAPL": {"n_days": 100}},
        )
        result = compute(data, _CONFIG)
        # AAPL should get neutral scores (50) for all sub-factors
        for col in COLS:
            assert result.loc["AAPL", col] == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# Relative strength vs ETF
# ---------------------------------------------------------------------------


class TestRelativeStrength:
    def test_outperformer_scores_higher_than_underperformer(self):
        # Stock A grows fast, Stock B flat — both in IT sector
        # ETF (XLK) grows moderately
        n = 300
        dates = pd.date_range("2023-01-01", periods=n, freq="B")

        def prices_df(ticker, growth):
            p = [100 * (1 + growth) ** i for i in range(n)]
            return pd.DataFrame(
                {"ticker": ticker, "date": dates, "adj_close": p, "close": p, "volume": 1000}
            )

        prices = pd.concat(
            [
                prices_df("FAST", 0.003),  # strong outperformer
                prices_df("SLOW", 0.0001),  # underperformer
                prices_df("XLK", 0.001),  # sector ETF
            ]
        )
        data = {
            "prices": prices,
            "universe": _make_universe(
                [("FAST", "Information Technology"), ("SLOW", "Information Technology")]
            ),
        }
        result = compute(data, _CONFIG)
        assert result.loc["FAST", "mom_rel_strength"] > result.loc["SLOW", "mom_rel_strength"]

    def test_no_etf_in_prices_yields_50_for_rel_strength(self):
        data = _make_data([("AAPL", "Information Technology"), ("MSFT", "Information Technology")])
        # No XLK in prices
        result = compute(data, _CONFIG)
        # Both tickers get 50 for rel_strength (no ETF data → NaN → 50)
        assert result.loc["AAPL", "mom_rel_strength"] == pytest.approx(50.0)
        assert result.loc["MSFT", "mom_rel_strength"] == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# 52-week high proximity
# ---------------------------------------------------------------------------


class TestHighProximity:
    def test_at_high_scores_highest(self):
        # Build two tickers: one near 52w high, one far from it
        n = 300
        dates = pd.date_range("2023-01-01", periods=n, freq="B")

        # Near-high: price ends at 100, 52w max = 101
        near_prices = [99 + 0.003 * i for i in range(n)]
        near_prices[-1] = 100.0

        # Far-from-high: price peaked early then drifted down
        far_prices = [100 - 0.001 * i for i in range(n)]

        prices = pd.DataFrame(
            [
                *[
                    {"ticker": "NEAR", "date": d, "adj_close": p, "close": p, "volume": 1}
                    for d, p in zip(dates, near_prices, strict=False)
                ],
                *[
                    {"ticker": "FAR", "date": d, "adj_close": p, "close": p, "volume": 1}
                    for d, p in zip(dates, far_prices, strict=False)
                ],
            ]
        )
        data = {
            "prices": prices,
            "universe": _make_universe([("NEAR", "IT"), ("FAR", "IT")]),
        }
        result = compute(data, _CONFIG)
        assert result.loc["NEAR", "mom_52w_high"] > result.loc["FAR", "mom_52w_high"]


# ---------------------------------------------------------------------------
# Momentum composite score
# ---------------------------------------------------------------------------


class TestMomentumScore:
    def test_higher_momentum_ticker_scores_higher(self):
        n = 300
        dates = pd.date_range("2023-01-01", periods=n, freq="B")

        def prices_df(ticker, growth):
            p = [100 * (1 + growth) ** i for i in range(n)]
            return pd.DataFrame(
                {"ticker": ticker, "date": dates, "adj_close": p, "close": p, "volume": 1000}
            )

        prices = pd.concat([prices_df("HIGH", 0.003), prices_df("LOW", 0.0001)])
        data = {
            "prices": prices,
            "universe": _make_universe([("HIGH", "IT"), ("LOW", "IT")]),
        }
        result = compute(data, _CONFIG)
        assert result.loc["HIGH", "momentum_score"] > result.loc["LOW", "momentum_score"]
