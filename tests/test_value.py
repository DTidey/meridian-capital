"""Tests for factors/value.py."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from factors.value import compute, COLS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fund_row(ticker, **overrides):
    base = dict(
        ticker=ticker, period_type="quarterly", period_end="2024-03-31",
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
    base.update(overrides)
    return base


def _make_data(tickers, prices=None, fund_overrides=None, estimates=None):
    fund_overrides = fund_overrides or {}
    universe = pd.DataFrame(
        [(t, "IT") for t in tickers],
        columns=["ticker", "sector"],
    )
    if prices is None:
        prices_rows = [{"ticker": t, "date": pd.Timestamp("2024-06-01"),
                        "adj_close": 100.0, "close": 100.0, "volume": 1000}
                       for t in tickers]
        prices = pd.DataFrame(prices_rows)

    fund_rows = [_fund_row(t, **fund_overrides.get(t, {})) for t in tickers]
    funds = pd.DataFrame(fund_rows) if fund_rows else pd.DataFrame(columns=list(_fund_row("X").keys()))
    if not funds.empty:
        funds["period_end"] = pd.to_datetime(funds["period_end"])

    if estimates is None:
        est_rows = [{"ticker": t, "date": pd.Timestamp("2024-06-01"),
                     "eps_estimate_fwd": 5.0, "price_target": 120.0, "num_analysts": 10}
                    for t in tickers]
        estimates = pd.DataFrame(est_rows)

    return {
        "universe": universe,
        "prices": prices,
        "fundamentals": funds,
        "estimates": estimates,
    }


_CONFIG = {"scoring": {"min_sector_size": 2}}


# ---------------------------------------------------------------------------
# Structure tests
# ---------------------------------------------------------------------------

class TestStructure:
    def test_columns_present(self):
        data = _make_data(["AAPL", "MSFT"])
        result = compute(data, _CONFIG)
        assert set(COLS + ["value_score"]).issubset(result.columns)

    def test_scores_0_to_100(self):
        data = _make_data(["AAPL", "MSFT", "GOOG"])
        result = compute(data, _CONFIG)
        for col in COLS + ["value_score"]:
            assert result[col].between(0, 100).all(), f"{col} out of range"

    def test_empty_universe_returns_empty(self):
        data = _make_data([])
        result = compute(data, _CONFIG)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# Forward earnings yield
# ---------------------------------------------------------------------------

class TestFwdEarningsYield:
    def test_higher_eps_relative_to_price_scores_higher(self):
        # CHEAP: eps=10, price=100 → yield 10%
        # EXPEN: eps=1,  price=100 → yield 1%
        data = _make_data(
            ["CHEAP", "EXPEN"],
            fund_overrides={
                "CHEAP": {"shares_outstanding": 1e8},
                "EXPEN": {"shares_outstanding": 1e8},
            },
            estimates=pd.DataFrame([
                {"ticker": "CHEAP", "date": pd.Timestamp("2024-06-01"),
                 "eps_estimate_fwd": 10.0, "price_target": 120.0, "num_analysts": 5},
                {"ticker": "EXPEN", "date": pd.Timestamp("2024-06-01"),
                 "eps_estimate_fwd": 1.0,  "price_target": 120.0, "num_analysts": 5},
            ]),
        )
        result = compute(data, _CONFIG)
        assert result.loc["CHEAP", "val_fwd_earn_yield"] > result.loc["EXPEN", "val_fwd_earn_yield"]


# ---------------------------------------------------------------------------
# EV/EBITDA inverted
# ---------------------------------------------------------------------------

class TestEvEbitda:
    def test_negative_ebit_yields_nan_then_50(self):
        data = _make_data(["AAPL", "MSFT"], fund_overrides={"AAPL": {"ebit": -1e8}})
        result = compute(data, _CONFIG)
        # AAPL has negative ebit → NaN → gets 50
        assert result.loc["AAPL", "val_ev_ebitda_inv"] == pytest.approx(50.0)

    def test_cheaper_ev_scores_higher(self):
        # LOW_EV: low debt/cash → smaller EV relative to ebit
        # HIGH_EV: big debt → larger EV
        data = _make_data(
            ["LOW_EV", "HIGH_EV"],
            fund_overrides={
                "LOW_EV":  {"total_debt": 1e7,  "cash": 1e8, "ebit": 2e8},
                "HIGH_EV": {"total_debt": 5e9,  "cash": 1e7, "ebit": 2e8},
            },
        )
        result = compute(data, _CONFIG)
        assert result.loc["LOW_EV", "val_ev_ebitda_inv"] > result.loc["HIGH_EV", "val_ev_ebitda_inv"]


# ---------------------------------------------------------------------------
# Missing data
# ---------------------------------------------------------------------------

class TestMissingData:
    def test_missing_price_yields_all_50(self):
        data = _make_data(["AAPL", "MSFT"])
        # Remove AAPL from prices
        data["prices"] = data["prices"][data["prices"]["ticker"] != "AAPL"]
        result = compute(data, _CONFIG)
        for col in COLS:
            assert result.loc["AAPL", col] == pytest.approx(50.0)

    def test_missing_fundamentals_yields_all_50(self):
        data = _make_data(["AAPL", "MSFT"])
        data["fundamentals"] = data["fundamentals"][data["fundamentals"]["ticker"] != "AAPL"]
        result = compute(data, _CONFIG)
        for col in COLS:
            assert result.loc["AAPL", col] == pytest.approx(50.0)

    def test_missing_estimates_yields_50_for_fwd_yield(self):
        data = _make_data(["AAPL", "MSFT"], estimates=pd.DataFrame(
            columns=["ticker", "date", "eps_estimate_fwd", "price_target", "num_analysts"]
        ))
        result = compute(data, _CONFIG)
        assert result.loc["AAPL", "val_fwd_earn_yield"] == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# Shareholder yield
# ---------------------------------------------------------------------------

class TestShareholderYield:
    def test_higher_return_of_capital_scores_higher(self):
        data = _make_data(
            ["HIGH_YIELD", "LOW_YIELD"],
            fund_overrides={
                "HIGH_YIELD": {"dividends_paid": -5e8, "buybacks": -3e8},
                "LOW_YIELD":  {"dividends_paid": -1e6, "buybacks": -1e6},
            },
        )
        result = compute(data, _CONFIG)
        assert result.loc["HIGH_YIELD", "val_shareholder_yield"] > result.loc["LOW_YIELD", "val_shareholder_yield"]
