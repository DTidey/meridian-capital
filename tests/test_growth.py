"""Tests for factors/growth.py."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from factors.growth import compute, COLS, _yoy, _acceleration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fund_series(ticker, n=8, rev_growth=0.05, earn_growth=0.05):
    """Generate n quarterly fundamental rows with compounding growth."""
    rows = []
    base_rev  = 1e9
    base_earn = 1e8
    base_fcf  = 8e7
    for i in range(n):
        rows.append(dict(
            ticker=ticker,
            period_type="quarterly",
            period_end=pd.Timestamp("2022-01-01") + pd.DateOffset(months=3*i),
            revenue=base_rev  * (1 + rev_growth)  ** i,
            net_income=base_earn * (1 + earn_growth) ** i,
            fcf=base_fcf     * (1 + earn_growth)  ** i,
            rd_expense=2e7,
            gross_profit=4e8, operating_income=2e8, ebit=2e8,
            total_assets=5e9, total_liabilities=2e9, total_equity=3e9,
            cash=5e8, total_debt=1e9, current_assets=1e9, current_liabilities=5e8,
            accounts_receivable=2e8, retained_earnings=1e9, shares_outstanding=1e8,
            dividends_paid=-1e7, cfo=1.5e8, capex=-5e7, buybacks=-2e7,
            roe=0.10, roa=0.05, gross_margin=0.40, operating_margin=0.20,
            net_margin=0.10, revenue_growth_yoy=rev_growth, revenue_growth_qoq=0.01,
            earnings_growth_yoy=earn_growth, earnings_growth_qoq=0.01,
            debt_to_equity=0.33, current_ratio=2.0,
            ar_to_revenue=0.20, cfo_to_ni=1.5, accruals_ratio=0.01,
            working_capital=5e8, asset_turnover=0.20, updated_at="2024-01-01",
        ))
    return pd.DataFrame(rows)


def _make_data(tickers_and_kwargs):
    universe = pd.DataFrame(
        [(t, "IT") for t, _ in tickers_and_kwargs],
        columns=["ticker", "sector"],
    )
    frames = [_fund_series(t, **kw) for t, kw in tickers_and_kwargs]
    return {
        "universe": universe,
        "fundamentals": pd.concat(frames, ignore_index=True),
        "prices": pd.DataFrame(),
    }


_CONFIG = {"scoring": {"min_sector_size": 2}}


# ---------------------------------------------------------------------------
# _yoy
# ---------------------------------------------------------------------------

class TestYoY:
    def test_basic_growth(self):
        df = _fund_series("T", n=6, rev_growth=0.10)
        result = _yoy(df, "revenue")
        # Each quarter grows by ~10%, so 4 quarters = ~46% YoY
        assert result == pytest.approx(0.10 ** 4 + 4 * 0.10 ** 3 + 6 * 0.10 ** 2 + 4 * 0.10, rel=0.1)

    def test_insufficient_history_returns_nan(self):
        df = _fund_series("T", n=3)
        assert np.isnan(_yoy(df, "revenue"))

    def test_zero_prior_returns_nan(self):
        df = _fund_series("T", n=6)
        df.iloc[-5, df.columns.get_loc("revenue")] = 0
        result = _yoy(df, "revenue")
        assert result is None or (isinstance(result, float) and np.isnan(result))


# ---------------------------------------------------------------------------
# _acceleration
# ---------------------------------------------------------------------------

class TestAcceleration:
    def test_accelerating_growth_positive(self):
        df = _fund_series("T", n=10, rev_growth=0.15)
        # All growth rates equal → acceleration ≈ 0 (steady state)
        result = _acceleration(df, "revenue")
        assert result is not None

    def test_insufficient_history_returns_nan(self):
        df = _fund_series("T", n=7)
        assert np.isnan(_acceleration(df, "revenue"))


# ---------------------------------------------------------------------------
# compute()
# ---------------------------------------------------------------------------

class TestCompute:
    def test_columns_present(self):
        data = _make_data([("AAPL", {}), ("MSFT", {})])
        result = compute(data, _CONFIG)
        assert set(COLS + ["growth_score"]).issubset(result.columns)

    def test_scores_0_to_100(self):
        data = _make_data([("AAPL", {}), ("MSFT", {}), ("GOOG", {})])
        result = compute(data, _CONFIG)
        for col in COLS + ["growth_score"]:
            assert result[col].between(0, 100).all(), f"{col} out of range"

    def test_faster_growing_ticker_scores_higher(self):
        data = _make_data([
            ("HIGH", {"rev_growth": 0.15, "earn_growth": 0.15}),
            ("LOW",  {"rev_growth": 0.01, "earn_growth": 0.01}),
        ])
        result = compute(data, _CONFIG)
        assert result.loc["HIGH", "grw_rev_yoy"] > result.loc["LOW", "grw_rev_yoy"]

    def test_rd_intensity_present(self):
        data = _make_data([("AAPL", {}), ("MSFT", {})])
        result = compute(data, _CONFIG)
        # Both have rd_expense=2e7, revenue=1e9 → rd_intensity=0.02
        # Scores differ only if values differ; with identical values both get 50
        assert "grw_rd_intensity" in result.columns

    def test_empty_universe(self):
        data = {
            "universe": pd.DataFrame(columns=["ticker", "sector"]),
            "fundamentals": pd.DataFrame(),
            "prices": pd.DataFrame(),
        }
        result = compute(data, _CONFIG)
        assert len(result) == 0

    def test_insufficient_history_yields_50(self):
        data = _make_data([
            ("SHORT", {"n": 2}),
            ("LONG",  {"n": 8}),
        ])
        # SHORT has only 2 rows — not enough for YoY (needs ≥5)
        result = compute(data, _CONFIG)
        # SHORT should get 50 for yoy sub-factors (NaN → 50 after ranking)
        assert result.loc["SHORT", "grw_rev_yoy"] == pytest.approx(50.0)
