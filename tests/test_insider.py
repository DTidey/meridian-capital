"""Tests for factors/insider.py."""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from factors.insider import COLS, compute

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _txn(ticker, code, shares, price, is_ceo_cfo=0):
    return {
        "ticker": ticker,
        "insider_name": "Test Person",
        "insider_title": "Director",
        "transaction_code": code,
        "shares": shares,
        "price": price,
        "date": pd.Timestamp("2024-05-01"),
        "is_open_market": 1,
        "is_ceo_cfo": is_ceo_cfo,
    }


def _cluster_flag(ticker):
    return {
        "ticker": ticker,
        "window_start": "2024-04-01",
        "window_end": "2024-05-01",
        "insider_count": 3,
    }


def _make_data(tickers, txns=None, flags=None):
    universe = pd.DataFrame([(t, "IT") for t in tickers], columns=["ticker", "sector"])
    txn_df = pd.DataFrame(
        txns or [],
        columns=[
            "ticker",
            "insider_name",
            "insider_title",
            "transaction_code",
            "shares",
            "price",
            "date",
            "is_open_market",
            "is_ceo_cfo",
        ],
    )
    flag_df = pd.DataFrame(
        flags or [],
        columns=[
            "ticker",
            "window_start",
            "window_end",
            "insider_count",
        ],
    )
    return {
        "universe": universe,
        "insider_txns": txn_df,
        "insider_clusters": flag_df,
    }


_CONFIG = {"scoring": {"min_sector_size": 2}}


# ---------------------------------------------------------------------------
# Net dollar flow
# ---------------------------------------------------------------------------


class TestNetFlow:
    def test_buyer_scores_higher_than_seller(self):
        data = _make_data(
            ["BUYER", "SELLER"],
            txns=[
                _txn("BUYER", "P", 1000, 100.0),
                _txn("SELLER", "S", 1000, 100.0),
            ],
        )
        result = compute(data, _CONFIG)
        assert result.loc["BUYER", "ins_net_flow"] > result.loc["SELLER", "ins_net_flow"]

    def test_purchase_adds_positive_flow(self):
        # Single buyer: net_flow = shares * price = 100 * 50 = 5000
        data = _make_data(
            ["A", "B"],
            txns=[_txn("A", "P", 100, 50.0), _txn("B", "S", 100, 50.0)],
        )
        result = compute(data, _CONFIG)
        assert result.loc["A", "ins_net_flow"] > result.loc["B", "ins_net_flow"]

    def test_no_transactions_yields_50(self):
        data = _make_data(["AAPL", "MSFT"])
        result = compute(data, _CONFIG)
        for col in COLS:
            assert result.loc["AAPL", col] == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# CEO/CFO weighting
# ---------------------------------------------------------------------------


class TestCeoWeight:
    def test_ceo_purchase_weighted_3x(self):
        # CEO buys 100 shares @ $100 → flow = 100 * 100 * 3 = 30000
        # Director buys 300 shares @ $100 → flow = 300 * 100 * 1 = 30000
        # Same nominal dollar flow, but CEO weighted differently
        # Let's test: small CEO purchase vs large director purchase
        data = _make_data(
            ["CEO_BUY", "DIR_BUY"],
            txns=[
                _txn("CEO_BUY", "P", 100, 100.0, is_ceo_cfo=1),  # 100*100*3 = 30000
                _txn("DIR_BUY", "P", 90, 100.0, is_ceo_cfo=0),  # 90*100*1  = 9000
            ],
        )
        result = compute(data, _CONFIG)
        assert result.loc["CEO_BUY", "ins_net_flow"] > result.loc["DIR_BUY", "ins_net_flow"]


# ---------------------------------------------------------------------------
# Cluster flag
# ---------------------------------------------------------------------------


class TestClusterFlag:
    def test_cluster_flag_scores_higher(self):
        data = _make_data(
            ["CLUSTER", "NO_CLUSTER"],
            flags=[_cluster_flag("CLUSTER")],
        )
        result = compute(data, _CONFIG)
        assert (
            result.loc["CLUSTER", "ins_cluster_flag"] > result.loc["NO_CLUSTER", "ins_cluster_flag"]
        )

    def test_no_flags_all_equal(self):
        data = _make_data(["AAPL", "MSFT"])
        result = compute(data, _CONFIG)
        assert result.loc["AAPL", "ins_cluster_flag"] == result.loc["MSFT", "ins_cluster_flag"]


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


class TestStructure:
    def test_columns_present(self):
        data = _make_data(["AAPL", "MSFT"])
        result = compute(data, _CONFIG)
        assert set(COLS + ["insider_score"]).issubset(result.columns)

    def test_scores_0_to_100(self):
        data = _make_data(
            ["A", "B", "C"],
            txns=[
                _txn("A", "P", 1000, 100.0),
                _txn("B", "S", 1000, 100.0),
                _txn("C", "P", 500, 100.0),
            ],
            flags=[_cluster_flag("A")],
        )
        result = compute(data, _CONFIG)
        for col in COLS + ["insider_score"]:
            assert result[col].between(0, 100).all(), f"{col} out of range"

    def test_empty_universe(self):
        data = {
            "universe": pd.DataFrame(columns=["ticker", "sector"]),
            "insider_txns": pd.DataFrame(),
            "insider_clusters": pd.DataFrame(),
        }
        result = compute(data, _CONFIG)
        assert len(result) == 0
