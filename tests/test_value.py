"""Tests for factors/value.py."""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from factors.value import COLS, compute

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fund_row(ticker, **overrides):
    base = {
        "ticker": ticker,
        "period_type": "quarterly",
        "period_end": "2024-03-31",
        "revenue": 1e9,
        "gross_profit": 4e8,
        "operating_income": 2e8,
        "ebit": 2e8,
        "net_income": 1e8,
        "rd_expense": 0.0,
        "total_assets": 5e9,
        "total_liabilities": 2e9,
        "total_equity": 3e9,
        "cash": 5e8,
        "total_debt": 1e9,
        "current_assets": 1e9,
        "current_liabilities": 5e8,
        "accounts_receivable": 2e8,
        "retained_earnings": 1e9,
        "shares_outstanding": 1e8,
        "dividends_paid": -1e7,
        "cfo": 1.5e8,
        "capex": -5e7,
        "fcf": 1e8,
        "buybacks": -2e7,
        "roe": 0.10,
        "roa": 0.05,
        "gross_margin": 0.40,
        "operating_margin": 0.20,
        "net_margin": 0.10,
        "revenue_growth_yoy": 0.05,
        "revenue_growth_qoq": 0.01,
        "earnings_growth_yoy": 0.05,
        "earnings_growth_qoq": 0.01,
        "debt_to_equity": 0.33,
        "current_ratio": 2.0,
        "ar_to_revenue": 0.20,
        "cfo_to_ni": 1.5,
        "accruals_ratio": 0.01,
        "working_capital": 5e8,
        "asset_turnover": 0.20,
        "updated_at": "2024-01-01",
    }
    base.update(overrides)
    return base


def _trend_rows(eps_by_ticker, days_7=None):
    """Latest +1y trend snapshot per ticker; days_7 defaults to current (no glitch)."""
    days_7 = days_7 or {}
    return pd.DataFrame(
        [
            {
                "ticker": t,
                "date": pd.Timestamp("2024-06-01"),
                "current": eps,
                "days_7": days_7.get(t, eps),
                "days_30": eps,
                "days_60": eps,
                "days_90": eps,
            }
            for t, eps in eps_by_ticker.items()
        ],
        columns=["ticker", "date", "current", "days_7", "days_30", "days_60", "days_90"],
    )


def _make_data(tickers, prices=None, fund_overrides=None, trends=None):
    fund_overrides = fund_overrides or {}
    universe = pd.DataFrame(
        [(t, "IT") for t in tickers],
        columns=["ticker", "sector"],
    )
    if prices is None:
        prices_rows = [
            {
                "ticker": t,
                "date": pd.Timestamp("2024-06-01"),
                "adj_close": 100.0,
                "close": 100.0,
                "volume": 1000,
            }
            for t in tickers
        ]
        prices = pd.DataFrame(prices_rows)

    fund_rows = [_fund_row(t, **fund_overrides.get(t, {})) for t in tickers]
    funds = (
        pd.DataFrame(fund_rows) if fund_rows else pd.DataFrame(columns=list(_fund_row("X").keys()))
    )
    if not funds.empty:
        funds["period_end"] = pd.to_datetime(funds["period_end"])

    if trends is None:
        trends = _trend_rows(dict.fromkeys(tickers, 5.0))

    return {
        "universe": universe,
        "prices": prices,
        "fundamentals": funds,
        "estimate_trends": trends,
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
            trends=_trend_rows({"CHEAP": 10.0, "EXPEN": 1.0}),
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
                "LOW_EV": {"total_debt": 1e7, "cash": 1e8, "ebit": 2e8},
                "HIGH_EV": {"total_debt": 5e9, "cash": 1e7, "ebit": 2e8},
            },
        )
        result = compute(data, _CONFIG)
        assert (
            result.loc["LOW_EV", "val_ev_ebitda_inv"] > result.loc["HIGH_EV", "val_ev_ebitda_inv"]
        )


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

    def test_missing_trend_yields_50_for_fwd_yield(self):
        data = _make_data(
            ["AAPL", "MSFT"],
            trends=_trend_rows({}),
        )
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
                "LOW_YIELD": {"dividends_paid": -1e6, "buybacks": -1e6},
            },
        )
        result = compute(data, _CONFIG)
        assert (
            result.loc["HIGH_YIELD", "val_shareholder_yield"]
            > result.loc["LOW_YIELD", "val_shareholder_yield"]
        )


# ---------------------------------------------------------------------------
# Packet 41: forward yield from the guarded +1y trend (AC5, AC7)
# ---------------------------------------------------------------------------


class TestFwdYieldFromTrend:
    def test_yield_is_plus_1y_current_over_price(self):
        data = _make_data(["A", "B", "C"], trends=_trend_rows({"A": 4.0, "B": 6.0, "C": 5.0}))
        result = compute(data, _CONFIG)
        assert result["val_fwd_earn_yield"].tolist() == pytest.approx([100 / 3, 100.0, 200 / 3])

    def test_no_trend_snapshot_neutral(self):
        data = _make_data(["A", "B", "C"], trends=_trend_rows({"A": 4.0, "B": 6.0}))
        result = compute(data, _CONFIG)
        assert result.loc["C", "val_fwd_earn_yield"] == pytest.approx(50.0)

    def test_analyst_estimates_not_read(self):
        data = _make_data(["CHEAP", "EXPEN"], trends=_trend_rows({"CHEAP": 10.0, "EXPEN": 1.0}))
        assert "estimates" not in data
        result = compute(data, _CONFIG)
        assert result.loc["CHEAP", "val_fwd_earn_yield"] > result.loc["EXPEN", "val_fwd_earn_yield"]

    # Adversarial: a negative forward EPS is a negative yield, ranked lowest, not dropped.
    def test_negative_eps_ranks_lowest(self):
        data = _make_data(["A", "B", "LOSS"], trends=_trend_rows({"A": 4.0, "B": 6.0, "LOSS": -2.0}))
        result = compute(data, _CONFIG)
        assert result.loc["LOSS", "val_fwd_earn_yield"] == pytest.approx(100 / 3)


class TestGlitchGuard:
    def test_pgr_glitch_uses_days_7(self):
        trends = _trend_rows(
            {"LOW": 10.0, "PGR": 1012.0, "HIGH": 20.0}, days_7={"PGR": 16.36848}
        )
        result = compute(_make_data(["LOW", "PGR", "HIGH"], trends=trends), _CONFIG)
        # 16.37 / 100 sits between 0.10 and 0.20; the raw 1012 would have ranked first.
        assert result.loc["PGR", "val_fwd_earn_yield"] == pytest.approx(200 / 3)
        assert result.loc["HIGH", "val_fwd_earn_yield"] == pytest.approx(100.0)
