"""Tests for portfolio/transaction_costs.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import pandas as pd
import pytest

import portfolio.db  # noqa: F401
from portfolio.transaction_costs import compute_adv, estimate_cost, net_expected_return

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CONFIG = {
    "portfolio": {
        "transaction_costs": {
            "spread_hl_fraction": 0.05,
            "market_impact_coef": 0.10,
        },
        "adv_lookback_days": 20,
    }
}


def _make_prices_df(n=20, close=100.0, high=101.0, low=99.0, volume=10_000):
    """Build a plain DataFrame with OHLCV columns."""
    return pd.DataFrame(
        {
            "close": [float(close)] * n,
            "high": [float(high)] * n,
            "low": [float(low)] * n,
            "volume": [float(volume)] * n,
        }
    )


# ---------------------------------------------------------------------------
# estimate_cost
# ---------------------------------------------------------------------------


class TestEstimateCost:
    def test_zero_trade_shares_gives_zero_cost(self):
        prices_df = _make_prices_df(n=20)
        cost = estimate_cost("AAPL", 0, 100.0, prices_df, _CONFIG)
        assert cost == pytest.approx(0.0, abs=0.01)

    def test_cost_scales_with_trade_size(self):
        prices_df = _make_prices_df(n=20)
        cost_small = estimate_cost("AAPL", 10, 100.0, prices_df, _CONFIG)
        cost_large = estimate_cost("AAPL", 100, 100.0, prices_df, _CONFIG)
        assert cost_large > cost_small

    def test_spread_component_proportional_to_hl_range(self):
        """Wider H-L spread → higher cost."""
        prices_tight = _make_prices_df(n=20, high=100.5, low=99.5)
        prices_wide = _make_prices_df(n=20, high=105.0, low=95.0)
        cost_tight = estimate_cost("AAPL", 50, 100.0, prices_tight, _CONFIG)
        cost_wide = estimate_cost("AAPL", 50, 100.0, prices_wide, _CONFIG)
        assert cost_wide > cost_tight

    def test_empty_prices_gives_zero_cost(self):
        empty_df = pd.DataFrame()
        cost = estimate_cost("AAPL", 100, 100.0, empty_df, _CONFIG)
        assert cost == pytest.approx(0.0, abs=0.01)


# ---------------------------------------------------------------------------
# net_expected_return
# ---------------------------------------------------------------------------


class TestNetExpectedReturn:
    def test_net_return_deducts_cost(self):
        gross = 0.10
        cost_usd = 5_000.0
        pos_value = 100_000.0
        net = net_expected_return(gross, cost_usd, pos_value)
        expected = gross - cost_usd / pos_value
        assert net == pytest.approx(expected, rel=0.05)

    def test_net_return_unchanged_when_zero_value(self):
        gross = 0.08
        net = net_expected_return(gross, 1_000.0, 0)
        assert net == pytest.approx(gross, rel=0.05)


# ---------------------------------------------------------------------------
# compute_adv
# ---------------------------------------------------------------------------


class TestComputeAdv:
    def test_compute_adv_correct(self):
        """5 rows, volume=1000, close=100 → adv = 100_000."""
        prices_df = _make_prices_df(n=5, close=100.0, volume=1000)
        adv = compute_adv(prices_df, adv_days=5)
        assert adv == pytest.approx(100_000.0, rel=0.05)

    def test_empty_prices_gives_zero_adv(self):
        adv = compute_adv(pd.DataFrame(), adv_days=20)
        assert adv == pytest.approx(0.0)
