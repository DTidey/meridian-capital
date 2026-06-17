"""Tests for factors/quality.py."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from factors.quality import (
    compute, COLS,
    _roe_stability, _gm_trend, _de_inv, _cfo_to_ni,
    _accruals_inv, _piotroski, _altman_z,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fund_rows(ticker, n=4, **overrides):
    """Generate n quarterly rows for ticker."""
    rows = []
    for i in range(n):
        row = dict(
            ticker=ticker,
            period_type="quarterly",
            period_end=pd.Timestamp(f"202{3 + i//4}-{3*(i%4)+3:02d}-30"),
            revenue=1e9, gross_profit=4e8, operating_income=2e8, ebit=2e8,
            net_income=1e8, rd_expense=0.0, total_assets=5e9, total_liabilities=2e9,
            total_equity=3e9, cash=5e8, total_debt=1e9, current_assets=1e9,
            current_liabilities=5e8, accounts_receivable=2e8, retained_earnings=1e9,
            shares_outstanding=1e8, dividends_paid=-1e7, cfo=1.5e8, capex=-5e7,
            fcf=1e8, buybacks=-2e7, roe=0.10, roa=0.05, gross_margin=0.40,
            operating_margin=0.20, net_margin=0.10, revenue_growth_yoy=0.05,
            revenue_growth_qoq=0.01, earnings_growth_yoy=0.05, earnings_growth_qoq=0.01,
            debt_to_equity=0.33, current_ratio=2.0, ar_to_revenue=0.20,
            cfo_to_ni=1.5, accruals_ratio=0.01, working_capital=5e8, asset_turnover=0.20,
            updated_at="2024-01-01",
        )
        row.update(overrides)
        rows.append(row)
    return pd.DataFrame(rows)


def _make_data(tickers, fund_frames=None, price_map=None):
    universe = pd.DataFrame([(t, "IT") for t in tickers], columns=["ticker", "sector"])
    if fund_frames is None:
        fund_frames = {t: _fund_rows(t) for t in tickers}
    funds = pd.concat(list(fund_frames.values()), ignore_index=True)
    if price_map is None:
        price_map = {t: 100.0 for t in tickers}
    price_rows = [{"ticker": t, "date": pd.Timestamp("2024-06-01"),
                   "adj_close": p, "close": p, "volume": 1} for t, p in price_map.items()]
    return {
        "universe": universe,
        "fundamentals": funds,
        "prices": pd.DataFrame(price_rows),
    }


_CONFIG = {"scoring": {"min_sector_size": 2}}


# ---------------------------------------------------------------------------
# Piotroski F-Score
# ---------------------------------------------------------------------------

class TestPiotroski:
    def _two_rows(self, cur_overrides=None, prev_overrides=None):
        prev_row = dict(
            ticker="T", period_type="quarterly", period_end=pd.Timestamp("2024-01-01"),
            net_income=1e8, total_assets=5e9, cfo=1.5e8, debt_to_equity=0.50,
            current_ratio=1.8, shares_outstanding=1e8, gross_margin=0.38,
            asset_turnover=0.18, revenue=1e9, gross_profit=3.8e8,
            operating_income=2e8, ebit=2e8, rd_expense=0, total_liabilities=2e9,
            total_equity=3e9, cash=5e8, total_debt=1e9, current_assets=1e9,
            current_liabilities=5e8, accounts_receivable=2e8, retained_earnings=1e9,
            dividends_paid=-1e7, capex=-5e7, fcf=1e8, buybacks=-2e7,
            roe=0.10, roa=0.05, operating_margin=0.20, net_margin=0.10,
            revenue_growth_yoy=0.05, revenue_growth_qoq=0.01,
            earnings_growth_yoy=0.05, earnings_growth_qoq=0.01,
            ar_to_revenue=0.20, cfo_to_ni=1.5,
            accruals_ratio=0.01, working_capital=5e8, updated_at="2024-01-01",
        )
        cur_row = prev_row.copy()
        cur_row["period_end"] = pd.Timestamp("2024-04-01")
        cur_row["debt_to_equity"] = 0.40   # improved
        cur_row["current_ratio"]  = 2.0    # improved
        cur_row["gross_margin"]   = 0.40   # improved
        cur_row["asset_turnover"] = 0.20   # improved
        cur_row["net_income"]     = 1.1e8  # higher → ROA rises
        if cur_overrides:
            cur_row.update(cur_overrides)
        if prev_overrides:
            prev_row.update(prev_overrides)
        return pd.DataFrame([prev_row, cur_row])

    def test_all_positive_signals_gives_nine(self):
        df = self._two_rows()
        assert _piotroski(df) == pytest.approx(9.0)

    def test_negative_roa_penalises_signal_1(self):
        df = self._two_rows(cur_overrides={"net_income": -1e8})
        score = _piotroski(df)
        # Signals 1 (ROA>0) and 3 (rising ROA) should fail; CFO>NI still passes
        # since CFO=1.5e8 > NI=-1e8
        assert score <= 8.0
        assert score < 9.0

    def test_dilution_penalises_signal_7(self):
        df = self._two_rows(cur_overrides={"shares_outstanding": 2e8})  # diluted
        score = _piotroski(df)
        assert score <= 8.0

    def test_single_row_returns_nan(self):
        df = self._two_rows().iloc[:1]
        result = _piotroski(df)
        assert np.isnan(result)

    def test_score_between_0_and_9(self):
        df = self._two_rows()
        score = _piotroski(df)
        assert 0 <= score <= 9


# ---------------------------------------------------------------------------
# Altman Z-Score
# ---------------------------------------------------------------------------

class TestAltmanZ:
    def _row(self, **overrides):
        r = pd.Series(dict(
            working_capital=5e8, total_assets=5e9, retained_earnings=1e9,
            ebit=2e8, total_liabilities=2e9, revenue=1e9,
            shares_outstanding=1e8,
        ))
        for k, v in overrides.items():
            r[k] = v
        return r

    def test_healthy_firm_z_above_299(self):
        z = _altman_z(self._row(), price=100.0)
        assert z > 2.99

    def test_distressed_firm_z_below_181(self):
        # Heavy debt, negative retained earnings, losses
        z = _altman_z(self._row(
            total_liabilities=8e9, retained_earnings=-2e9, ebit=-5e8,
            working_capital=-1e8,
        ), price=5.0)
        assert z < 1.81

    def test_missing_field_returns_nan(self):
        r = self._row()
        r["working_capital"] = None
        assert np.isnan(_altman_z(r, price=100.0))

    def test_missing_price_returns_nan(self):
        assert np.isnan(_altman_z(self._row(), price=None))


# ---------------------------------------------------------------------------
# ROE stability
# ---------------------------------------------------------------------------

class TestRoeStability:
    def test_stable_roe_scores_higher_than_volatile(self):
        stable   = pd.DataFrame({"roe": [0.10] * 8})
        volatile = pd.DataFrame({"roe": [0.30, -0.20, 0.40, -0.10, 0.25, -0.15, 0.35, -0.05]})
        assert _roe_stability(stable) > _roe_stability(volatile)

    def test_single_quarter_returns_nan(self):
        assert np.isnan(_roe_stability(pd.DataFrame({"roe": [0.10]})))


# ---------------------------------------------------------------------------
# CFO / NI
# ---------------------------------------------------------------------------

class TestCfoToNi:
    def test_cfo_greater_than_ni_scores_higher(self):
        high = pd.Series({"cfo": 2e8, "net_income": 1e8})
        low  = pd.Series({"cfo": 5e7, "net_income": 1e8})
        assert _cfo_to_ni(high) > _cfo_to_ni(low)

    def test_zero_ni_returns_nan(self):
        row = pd.Series({"cfo": 1e8, "net_income": 0})
        assert np.isnan(_cfo_to_ni(row))


# ---------------------------------------------------------------------------
# Accruals
# ---------------------------------------------------------------------------

class TestAccruals:
    def test_high_accruals_penalised(self):
        low_accruals  = pd.Series({"net_income": 1e8, "cfo": 1.5e8, "total_assets": 5e9})
        high_accruals = pd.Series({"net_income": 1e8, "cfo": 1e7,   "total_assets": 5e9})
        assert _accruals_inv(low_accruals) > _accruals_inv(high_accruals)


# ---------------------------------------------------------------------------
# compute() integration
# ---------------------------------------------------------------------------

class TestCompute:
    def test_columns_present(self):
        data = _make_data(["AAPL", "MSFT"])
        result = compute(data, _CONFIG)
        assert set(COLS + ["quality_score"]).issubset(result.columns)

    def test_scores_0_to_100(self):
        data = _make_data(["AAPL", "MSFT", "GOOG"])
        result = compute(data, _CONFIG)
        for col in COLS + ["quality_score"]:
            assert result[col].between(0, 100).all(), f"{col} out of range"

    def test_no_fundamentals_yields_50(self):
        data = _make_data(["AAPL", "MSFT"])
        data["fundamentals"] = data["fundamentals"].iloc[0:0]  # empty
        result = compute(data, _CONFIG)
        # Tickers with no fundamentals get 50 (sector median) for all sub-factors
        assert not result.empty
        for col in COLS:
            assert result.loc["AAPL", col] == pytest.approx(50.0)
