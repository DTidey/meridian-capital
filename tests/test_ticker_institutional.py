"""Tests for dashboard/ticker_institutional.py — packet 18 AC1, AC2."""

from __future__ import annotations

import pytest

from dashboard.theme import LONG_COL, NEUTRAL, SHORT_COL
from dashboard.ticker_institutional import fund_holdings, net_change_display


def _row(fund, period, shares, value=None, filed="2026-08-14"):
    return {
        "fund_name": fund,
        "report_date": period,
        "filed_date": filed,
        "shares_held": shares,
        "market_value": value if value is not None else shares * 100,
    }


Q1, Q2 = "2026-03-31", "2026-06-30"


class TestFundHoldings:
    def test_change_vs_previous_filing(self):
        rows = [_row("Citadel", Q1, 1_000), _row("Citadel", Q2, 1_500)]
        (out,) = fund_holdings(rows, {"Citadel": [Q1, Q2]}, Q2)
        assert out["Fund"] == "Citadel"
        assert out["Quarter"] == Q2
        assert out["Shares"] == pytest.approx(1_500)
        assert out["Change"] == "+500"

    def test_new_position(self):
        rows = [_row("Point72", Q2, 2_000)]
        (out,) = fund_holdings(rows, {"Point72": [Q1, Q2]}, Q2)
        assert out["Change"] == "New"

    def test_first_filing_has_no_change(self):
        rows = [_row("Baupost", Q2, 2_000)]
        (out,) = fund_holdings(rows, {"Baupost": [Q2]}, Q2)
        assert out["Change"] == "—"

    def test_exited_fund_not_listed(self):
        # Citadel's latest filing no longer holds the ticker
        rows = [_row("Citadel", Q1, 1_000)]
        assert fund_holdings(rows, {"Citadel": [Q1, Q2]}, Q2) == []

    def test_fund_yet_to_file_carries_forward(self):
        rows = [_row("Point72", Q1, 3_000, filed="2026-05-15"), _row("Citadel", Q2, 1_000)]
        out = fund_holdings(rows, {"Point72": [Q1], "Citadel": [Q1, Q2]}, Q2)
        p72 = next(r for r in out if r["Fund"] == "Point72")
        assert p72["Quarter"] == Q1
        assert p72["Filed"] == "2026-05-15"

    def test_later_periods_ignored(self):
        # Adversarial: data after the as-of quarter must not leak in
        rows = [_row("Citadel", Q1, 1_000), _row("Citadel", "2026-09-30", 9_000)]
        (out,) = fund_holdings(rows, {"Citadel": [Q1, "2026-09-30"]}, Q1)
        assert out["Shares"] == pytest.approx(1_000)

    def test_sorted_by_value_desc_and_value_in_millions(self):
        rows = [
            _row("Small", Q2, 10, value=5_000_000),
            _row("Big", Q2, 10, value=65_950_300_000),
        ]
        out = fund_holdings(rows, {"Small": [Q2], "Big": [Q2]}, Q2)
        assert [r["Fund"] for r in out] == ["Big", "Small"]
        assert out[0]["Value ($M)"] == pytest.approx(65_950.3)

    def test_null_value_sorts_last(self):
        # Adversarial: a missing market value must not break sorting
        rows = [
            {**_row("NoValue", Q2, 10), "market_value": None},
            _row("HasValue", Q2, 10, value=1_000_000),
        ]
        out = fund_holdings(rows, {"NoValue": [Q2], "HasValue": [Q2]}, Q2)
        assert [r["Fund"] for r in out] == ["HasValue", "NoValue"]
        assert out[1]["Value ($M)"] is None

    def test_unchanged_position_shows_zero(self):
        rows = [_row("Berkshire", Q1, 34_249_183), _row("Berkshire", Q2, 34_249_183)]
        (out,) = fund_holdings(rows, {"Berkshire": [Q1, Q2]}, Q2)
        assert out["Change"] == "0"

    def test_negative_change_formatted(self):
        rows = [_row("Citadel", Q1, 2_000_000), _row("Citadel", Q2, 1_000_000)]
        (out,) = fund_holdings(rows, {"Citadel": [Q1, Q2]}, Q2)
        assert out["Change"] == "-1,000,000"


class TestNetChangeDisplay:
    def test_positive(self):
        text, colour = net_change_display(1_883_091.0)
        assert text == "+1.88M"
        assert colour == LONG_COL

    def test_negative(self):
        text, colour = net_change_display(-855_577.0)
        assert text == "-0.86M"
        assert colour == SHORT_COL

    def test_none_and_nan(self):
        assert net_change_display(None) == ("—", NEUTRAL)
        assert net_change_display(float("nan")) == ("—", NEUTRAL)

    def test_small_change_shown_in_shares(self):
        assert net_change_display(4_200.0)[0] == "+4,200"

    def test_zero(self):
        assert net_change_display(0.0) == ("0", NEUTRAL)
