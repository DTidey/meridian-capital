"""Value and quality inputs: market cap, TTM flows, one balance-sheet quarter, operating income,
equity rules (packet 42 AC3-AC8)."""

import numpy as np
import pandas as pd
import pytest

from factors import quality, value
from factors.quality import _de_inv, _piotroski, _roe_stability
from factors.value import _statement_yields
from tests.test_value import _CONFIG, _QUARTER_ENDS, _fund_row, _make_data

_ENDS_NEWEST_FIRST = list(reversed(_QUARTER_ENDS))


def _quarters(ticker="A", per_quarter=None, **overrides):
    """Four contiguous quarters, newest first; per_quarter maps index -> overrides."""
    per_quarter = per_quarter or {}
    return [
        _fund_row(ticker, period_end=end, **{**overrides, **per_quarter.get(i, {})})
        for i, end in enumerate(_ENDS_NEWEST_FIRST)
    ]


def _yields(quarters, mcap=1e10):
    keys = ("book", "fcf", "ebit_ev", "shareholder", "sales_ev")
    return dict(zip(keys, _statement_yields(quarters, mcap), strict=True))


def _snapshots(values):
    return pd.DataFrame(
        [(t, pd.Timestamp("2024-06-01"), v) for t, v in values.items()],
        columns=["ticker", "date", "market_cap"],
    )


# ---------------------------------------------------------------------------
# AC3: market cap from the snapshots
# ---------------------------------------------------------------------------


class TestMarketCapSource:
    def test_brk_book_to_price_from_snapshot(self):
        # Balance-sheet shares of 1.43M at $100 would make book/price 2,100x and rank first
        data = _make_data(
            ["BRK-B", "PEER1", "PEER2"],
            fund_overrides={"BRK-B": {"shares_outstanding": 1.43e6}},
        )
        data["market_caps"] = _snapshots({"BRK-B": 1.15e12, "PEER1": 1e10, "PEER2": 1e10})
        result = value.compute(data, _CONFIG)
        assert result.loc["BRK-B", "val_book_to_price"] == pytest.approx(100 / 3)

    def test_without_snapshots_balance_sheet_shares_used(self):
        data = _make_data(
            ["BRK-B", "PEER1", "PEER2"],
            fund_overrides={"BRK-B": {"shares_outstanding": 1.43e6}},
        )
        result = value.compute(data, _CONFIG)
        assert result.loc["BRK-B", "val_book_to_price"] == pytest.approx(100.0)

    def test_altman_z_uses_snapshot_market_cap(self):
        rows = [r for t in ("BIG", "SMALL") for r in _quarters(t)]
        funds = pd.DataFrame(rows)
        funds["period_end"] = pd.to_datetime(funds["period_end"])
        data = _make_data(["BIG", "SMALL"])
        data["fundamentals"] = funds
        data["market_caps"] = _snapshots({"BIG": 1e12, "SMALL": 1e9})
        result = quality.compute(data, _CONFIG)
        assert result.loc["BIG", "qual_altman_z"] > result.loc["SMALL", "qual_altman_z"]


# ---------------------------------------------------------------------------
# AC4: trailing twelve months
# ---------------------------------------------------------------------------


class TestTtm:
    def test_four_quarters_summed(self):
        y = _yields(_quarters(fcf=1e8, revenue=1e9))
        assert y["fcf"] == pytest.approx(4e8 / 1e10)

    def test_three_quarters_nan(self):
        y = _yields(_quarters()[:3])
        assert np.isnan(y["fcf"]) and np.isnan(y["ebit_ev"]) and np.isnan(y["sales_ev"])

    def test_gap_nan(self):
        ends = ("2024-03-31", "2023-12-31", "2023-06-30", "2023-03-31")  # 2023-09-30 missing
        rows = [_fund_row("A", period_end=end) for end in ends]
        assert np.isnan(_yields(rows)["fcf"])

    def test_single_quarter_ticker_scores_neutral(self):
        data = _make_data(["A", "B", "C"])
        funds = data["fundamentals"]
        data["fundamentals"] = funds[(funds["ticker"] != "C") | (funds["period_end"] >= "2024")]
        result = value.compute(data, _CONFIG)
        assert result.loc["C", "val_fcf_yield"] == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# AC5: omitted lines and NaN
# ---------------------------------------------------------------------------


class TestShareholderYield:
    def test_buybacks_without_dividend_line(self):
        # CMG: buys back stock, pays no dividend, so Yahoo has no dividend line
        y = _yields(_quarters(dividends_paid=np.nan, buybacks=-1.635e8))
        assert y["shareholder"] == pytest.approx(6.54e8 / 1e10)

    def test_neither_line_with_cash_flow_statement_is_zero(self):
        y = _yields(_quarters(dividends_paid=np.nan, buybacks=np.nan))
        assert y["shareholder"] == pytest.approx(0.0)

    def test_neither_line_without_cash_flow_statement_is_nan(self):
        y = _yields(_quarters(dividends_paid=np.nan, buybacks=np.nan, cfo=np.nan))
        assert np.isnan(y["shareholder"])

    def test_dividends_without_buyback_line(self):
        y = _yields(_quarters(dividends_paid=-1e7, buybacks=None))
        assert y["shareholder"] == pytest.approx(4e7 / 1e10)

    # Adversarial: NaN debt must not turn EV into NaN (the old `nan or 0.0` bug)
    def test_nan_debt_is_zero_debt(self):
        y = _yields(_quarters(total_debt=np.nan, cash=0.0, revenue=1e9))
        assert y["sales_ev"] == pytest.approx(4e9 / 1e10)


# ---------------------------------------------------------------------------
# AC6: operating income, not Yahoo's EBIT row
# ---------------------------------------------------------------------------


class TestOperatingIncome:
    def test_ev_ebit_uses_operating_income(self):
        # GOOGL 2026-06-30: EBIT row $140.0B, operating income $40.8B
        y = _yields(_quarters(ebit=140e9, operating_income=40e9, total_debt=0.0, cash=0.0), 1e12)
        assert y["ebit_ev"] == pytest.approx(160e9 / 1e12)

    def test_negative_operating_income_nan(self):
        assert np.isnan(_yields(_quarters(ebit=1e9, operating_income=-1e8))["ebit_ev"])


# ---------------------------------------------------------------------------
# AC7: balance sheet from one quarter
# ---------------------------------------------------------------------------


class TestOneBalanceSheetQuarter:
    def test_income_only_newest_quarter_uses_previous_balance_sheet(self):
        newest = {"total_assets": np.nan, "total_equity": np.nan, "cash": np.nan}
        y = _yields(_quarters(per_quarter={0: newest, 1: {"total_equity": 2e9}}))
        assert y["book"] == pytest.approx(2e9 / 1e10)

    # Adversarial: a missing cash figure must not be filled from an older quarter (M14)
    def test_missing_cash_not_taken_from_older_quarter(self):
        y = _yields(_quarters(per_quarter={0: {"cash": np.nan}}))
        assert np.isnan(y["ebit_ev"]) and np.isnan(y["sales_ev"])
        assert y["book"] == pytest.approx(3e9 / 1e10)


# ---------------------------------------------------------------------------
# AC8: equity rules in quality
# ---------------------------------------------------------------------------


class TestEquityRules:
    def test_negative_equity_with_debt_most_leveraged(self):
        assert _de_inv(pd.Series({"total_equity": -1e9, "total_debt": 5e9})) == -np.inf

    def test_negative_equity_without_debt_nan(self):
        assert np.isnan(_de_inv(pd.Series({"total_equity": -1e9, "total_debt": 0.0})))

    def test_omitted_debt_line_is_no_debt(self):
        assert _de_inv(pd.Series({"total_equity": 2e9, "total_debt": np.nan})) == 0.0

    def test_piotroski_leverage_from_raw_figures(self):
        # Stored ratios say D/E fell; the raw figures say it rose, so signal 5 is lost
        prev = _fund_row("A", period_end=pd.Timestamp("2024-03-31"), debt_to_equity=0.9)
        cur = _fund_row(
            "A", period_end=pd.Timestamp("2024-06-30"), debt_to_equity=0.1, total_debt=2e9
        )
        base = _piotroski(pd.DataFrame([prev, {**cur, "total_debt": 1e9}]))
        assert _piotroski(pd.DataFrame([prev, cur])) == base - 1

    def test_positive_equity(self):
        assert _de_inv(pd.Series({"total_equity": 2e9, "total_debt": 1e9})) == pytest.approx(-0.5)

    def test_negative_equity_ranks_lowest_in_sector(self):
        rows = [r for t in ("A", "B", "NEG") for r in _quarters(t)]
        funds = pd.DataFrame(rows)
        funds.loc[funds["ticker"] == "NEG", "total_equity"] = -1e9
        funds.loc[funds["ticker"] == "B", "total_debt"] = 2.9e9  # D/E near 1
        funds["period_end"] = pd.to_datetime(funds["period_end"])
        data = _make_data(["A", "B", "NEG"])
        data["fundamentals"] = funds
        result = quality.compute(data, _CONFIG)
        assert result.loc["NEG", "qual_de_inv"] == pytest.approx(100 / 3)

    def test_roe_stability_skips_non_positive_equity(self):
        df = pd.DataFrame(
            {"net_income": [1e8, 1e8, 1e8, 5e8], "total_equity": [1e9, 1e9, 1e9, -2e8]}
        )
        assert _roe_stability(df) == pytest.approx(0.0)
