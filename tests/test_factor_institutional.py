"""Tests for factors/institutional.py (Layer 2 factor scoring)."""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from factors.institutional import COLS, compute

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _inst_row(
    ticker, funds_holding=5, net_share_change=1e6, new_positions=0, report_date="2024-03-31"
):
    return {
        "ticker": ticker,
        "report_date": pd.Timestamp(report_date),
        "funds_holding": funds_holding,
        "net_share_change": net_share_change,
        "new_positions": new_positions,
    }


def _make_data(tickers, inst_rows=None):
    universe = pd.DataFrame([(t, "IT") for t in tickers], columns=["ticker", "sector"])
    rows = inst_rows if inst_rows is not None else [_inst_row(t) for t in tickers]
    institutional = (
        pd.DataFrame(rows)
        if rows
        else pd.DataFrame(
            columns=["ticker", "report_date", "funds_holding", "net_share_change", "new_positions"]
        )
    )
    return {"universe": universe, "institutional": institutional}


_CONFIG = {"scoring": {"min_sector_size": 2}}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFundsHolding:
    def test_more_funds_scores_higher(self):
        data = _make_data(
            ["POPULAR", "IGNORED"],
            inst_rows=[
                _inst_row("POPULAR", funds_holding=9),
                _inst_row("IGNORED", funds_holding=1),
            ],
        )
        result = compute(data, _CONFIG)
        assert (
            result.loc["POPULAR", "inst_funds_holding"]
            > result.loc["IGNORED", "inst_funds_holding"]
        )


class TestNetShareChange:
    def test_net_buying_scores_higher(self):
        data = _make_data(
            ["BOUGHT", "SOLD"],
            inst_rows=[
                _inst_row("BOUGHT", net_share_change=5e6),
                _inst_row("SOLD", net_share_change=-5e6),
            ],
        )
        result = compute(data, _CONFIG)
        assert (
            result.loc["BOUGHT", "inst_net_share_change"]
            > result.loc["SOLD", "inst_net_share_change"]
        )


class TestSimultaneousOpen:
    def test_3_plus_new_positions_flags(self):
        data = _make_data(
            ["FLAGGED", "UNFLAGGED"],
            inst_rows=[
                _inst_row("FLAGGED", new_positions=3),
                _inst_row("UNFLAGGED", new_positions=0),
            ],
        )
        result = compute(data, _CONFIG)
        assert (
            result.loc["FLAGGED", "inst_simultaneous_open"]
            > result.loc["UNFLAGGED", "inst_simultaneous_open"]
        )

    def test_2_new_positions_not_flagged(self):
        data = _make_data(
            ["A", "B"],
            inst_rows=[
                _inst_row("A", new_positions=2),
                _inst_row("B", new_positions=0),
            ],
        )
        result = compute(data, _CONFIG)
        # Both have raw value 0 → same rank → both 50
        assert (
            result.loc["A", "inst_simultaneous_open"] == result.loc["B", "inst_simultaneous_open"]
        )


class TestMissingData:
    def test_no_institutional_data_returns_all_50(self):
        data = {
            "universe": pd.DataFrame([("AAPL", "IT")], columns=["ticker", "sector"]),
            "institutional": pd.DataFrame(),
        }
        result = compute(data, _CONFIG)
        assert (result == 50.0).all().all()

    def test_ticker_missing_from_institutional_gets_50(self):
        data = _make_data(["AAPL", "MSFT"], inst_rows=[_inst_row("AAPL")])
        result = compute(data, _CONFIG)
        for col in COLS:
            assert result.loc["MSFT", col] == pytest.approx(50.0)


class TestStructure:
    def test_columns_present(self):
        data = _make_data(["AAPL", "MSFT"])
        result = compute(data, _CONFIG)
        assert set(COLS + ["institutional_score"]).issubset(result.columns)

    def test_scores_0_to_100(self):
        data = _make_data(
            ["AAPL", "MSFT", "GOOG"],
            inst_rows=[
                _inst_row("AAPL", funds_holding=9, net_share_change=5e6, new_positions=3),
                _inst_row("MSFT", funds_holding=5, net_share_change=0, new_positions=1),
                _inst_row("GOOG", funds_holding=1, net_share_change=-5e6, new_positions=0),
            ],
        )
        result = compute(data, _CONFIG)
        for col in COLS + ["institutional_score"]:
            assert result[col].between(0, 100).all(), f"{col} out of range"

    def test_latest_report_date_used(self):
        data = _make_data(
            ["AAPL", "MSFT"],
            inst_rows=[
                _inst_row("AAPL", funds_holding=1, report_date="2024-01-01"),
                _inst_row("AAPL", funds_holding=9, report_date="2024-03-31"),  # newer
                _inst_row("MSFT", funds_holding=5, report_date="2024-03-31"),
            ],
        )
        result = compute(data, _CONFIG)
        # AAPL latest = 9 funds → should score higher than MSFT (5)
        assert result.loc["AAPL", "inst_funds_holding"] > result.loc["MSFT", "inst_funds_holding"]
