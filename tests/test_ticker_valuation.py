"""Tests for dashboard/ticker_valuation.py — packet 21."""

from __future__ import annotations

import pytest

from dashboard.ticker_valuation import fmt_money, fmt_multiple, fmt_pct, valuation


def _q(period_end, **overrides):
    row = {
        "period_end": period_end,
        "net_income": 25e9,
        "ebit": 30e9,
        "fcf": 20e9,
        "dividends_paid": -4e9,
        "total_equity": 100e9,
        "total_debt": 80e9,
        "cash": 40e9,
        "shares_outstanding": 15e9,
    }
    row.update(overrides)
    return row


# AAPL quarters as stored on 2026-10-14 (newest first)
_AAPL = [
    _q(
        "2026-06-30",
        net_income=29_789e6,
        ebit=35_695e6,
        fcf=31_914e6,
        dividends_paid=-4_035e6,
        total_equity=107_520e6,
        total_debt=84_344e6,
        cash=39_544e6,
        shares_outstanding=14_687_356e3,
    ),
    _q("2026-03-31", net_income=29_578e6, ebit=35_885e6, fcf=26_731e6, dividends_paid=-3_822e6),
    _q("2025-12-31", net_income=42_097e6, ebit=50_852e6, fcf=51_552e6, dividends_paid=-3_921e6),
    _q("2025-09-30", net_income=27_466e6, ebit=32_427e6, fcf=26_486e6, dividends_paid=-3_862e6),
]


class TestValuation:
    def test_aapl(self):
        v = valuation(341.27, _AAPL)
        mcap = 341.27 * 14_687_356e3
        ev = mcap + 84_344e6 - 39_544e6
        assert v["market_cap"] == pytest.approx(mcap)
        assert v["ev"] == pytest.approx(ev)
        assert v["pe"] == pytest.approx(mcap / 128_930e6)
        assert v["ev_ebit"] == pytest.approx(ev / 154_859e6)
        assert v["pb"] == pytest.approx(mcap / 107_520e6)
        assert v["fcf_yield"] == pytest.approx(136_683e6 / mcap)
        assert v["div_yield"] == pytest.approx(15_640e6 / mcap)
        assert v["quarter_end"] == "2026-06-30"

    def test_fewer_than_four_quarters_no_ttm(self):
        v = valuation(100.0, _AAPL[:3])
        assert v["market_cap"] is not None
        assert v["pe"] is None and v["ev_ebit"] is None
        assert v["fcf_yield"] is None and v["div_yield"] is None
        assert v["pb"] is not None  # point-in-time, not TTM

    def test_gap_in_quarters_no_ttm(self):
        # Adversarial: four rows spanning more than 13 months aren't a trailing year
        rows = [_q("2026-06-30"), _q("2026-03-31"), _q("2025-12-31"), _q("2025-03-31")]
        assert valuation(100.0, rows)["pe"] is None

    def test_single_missing_quarter_no_ttm(self):
        # Reviewer: one missing quarter spans ~12 months, which must not count as a trailing year
        rows = [_q("2026-06-30"), _q("2026-03-31"), _q("2025-12-31"), _q("2025-06-30")]
        assert valuation(100.0, rows)["pe"] is None

    def test_52_53_week_quarter_ends_still_ttm(self):
        rows = [_q("2026-06-27"), _q("2026-03-28"), _q("2025-12-27"), _q("2025-09-27")]
        assert valuation(100.0, rows)["pe"] is not None

    def test_negative_ev_multiple_is_nm(self):
        rows = [
            _q(d, cash=10_000e9) for d in ("2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30")
        ]
        v = valuation(1.0, rows)
        assert v["ev"] < 0
        assert v["ev_ebit"] == "n/m"

    def test_dividend_signs_do_not_cancel(self):
        rows = [_q("2026-06-30", dividends_paid=-1e9), _q("2026-03-31", dividends_paid=1e9)]
        rows += [_q("2025-12-31", dividends_paid=-1e9), _q("2025-09-30", dividends_paid=-1e9)]
        assert valuation(1.0, rows)["div_yield"] == pytest.approx(4e9 / 15e9)

    def test_missing_value_in_ttm(self):
        rows = [_q("2026-06-30"), _q("2026-03-31", ebit=None), _q("2025-12-31"), _q("2025-09-30")]
        v = valuation(100.0, rows)
        assert v["ev_ebit"] is None
        assert v["pe"] is not None

    def test_bank_has_no_ebit_or_fcf(self):
        rows = [
            _q(d, ebit=None, fcf=None)
            for d in ("2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30")
        ]
        v = valuation(300.0, rows)
        assert v["ev_ebit"] is None and v["fcf_yield"] is None
        assert v["pe"] is not None

    def test_negative_denominators_are_nm(self):
        rows = [
            _q(d, net_income=-1e9, ebit=-2e9)
            for d in ("2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30")
        ]
        rows[0]["total_equity"] = -5e9
        v = valuation(100.0, rows)
        assert v["pe"] == "n/m" and v["ev_ebit"] == "n/m" and v["pb"] == "n/m"

    def test_missing_debt_or_cash_no_ev(self):
        v = valuation(100.0, [_q("2026-06-30", cash=None)] + _AAPL[1:])
        assert v["ev"] is None and v["ev_ebit"] is None
        assert v["market_cap"] is not None

    def test_negative_fcf_yield_kept(self):
        rows = [_q(d, fcf=-5e9) for d in ("2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30")]
        assert valuation(100.0, rows)["fcf_yield"] < 0

    def test_balance_sheet_from_latest_quarter_that_has_one(self):
        # BAC after packet 23: income for 2026-06-30 published, balance sheet not yet
        newest = _q(
            "2026-06-30", shares_outstanding=None, total_debt=None, cash=None, total_equity=None
        )
        v = valuation(50.0, [newest] + [_q(d) for d in ("2026-03-31", "2025-12-31", "2025-09-30")])
        assert v["market_cap"] == pytest.approx(50.0 * 15e9)
        assert v["quarter_end"] == "2026-03-31"
        assert v["pe"] == pytest.approx(50.0 * 15e9 / 100e9)  # TTM still the latest four

    def test_no_price_or_shares(self):
        assert valuation(None, _AAPL) is None
        assert valuation(100.0, []) is None
        assert valuation(100.0, [_q("2026-06-30", shares_outstanding=None)]) is None

    def test_zero_shares(self):
        # Adversarial: zero shares must not give a zero market cap and divide-by-zero yields
        assert valuation(100.0, [_q("2026-06-30", shares_outstanding=0)]) is None

    def test_nan_values_treated_as_missing(self):
        rows = [_q("2026-06-30", cash=float("nan"))] + _AAPL[1:]
        assert valuation(100.0, rows)["ev"] is None


class TestFormatting:
    @pytest.mark.parametrize(
        "value,text",
        [
            (5.0125e12, "$5.01T"),
            (84.34e9, "$84.3B"),
            (950.4e6, "$950M"),
            (None, "—"),
            (-5.04e9, "-$5.0B"),
            (950_000, "$950,000"),
            (-950_000, "-$950,000"),
            (999.96e9, "$1.00T"),
            (999.6e6, "$1.0B"),
        ],
    )
    def test_money(self, value, text):
        assert fmt_money(value) == text

    def test_multiple(self):
        assert fmt_multiple(38.874) == "38.9x"
        assert fmt_multiple("n/m") == "n/m"
        assert fmt_multiple(None) == "—"

    def test_pct(self):
        assert fmt_pct(0.02727) == "2.7%"
        assert fmt_pct(-0.013) == "-1.3%"
        assert fmt_pct(None) == "—"
