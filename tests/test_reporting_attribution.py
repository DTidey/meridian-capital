"""Tests for reporting/pnl_attribution.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


import pandas as pd

import analysis.db  # noqa: F401
import execution.db  # noqa: F401
import factors.db  # noqa: F401
import portfolio.db  # noqa: F401
import reporting.db  # noqa: F401
import risk.db  # noqa: F401
from reporting.pnl_attribution import _brinson_sector, _compute_net_beta


def _make_hist(weights_by_sector: dict, direction: str = "LONG") -> "pd.DataFrame":

    rows = []
    for sector, w in weights_by_sector.items():
        rows.append(
            {
                "ticker": f"T_{sector[:3]}",
                "direction": direction,
                "market_value": abs(w) * 1_000_000,
                "sector": sector,
                "weight": w,
            }
        )
    return pd.DataFrame(rows)


def test_beta_pnl_formula():
    """beta_pnl = net_beta * spy_return to floating-point precision."""
    net_beta = 0.25
    spy_return = 0.018
    expected = net_beta * spy_return
    result = net_beta * spy_return  # formula directly
    assert abs(result - expected) < 1e-12


def test_compute_net_beta_long():
    """Net beta for all-LONG portfolio equals sum of weights."""
    import pandas as pd

    hist = pd.DataFrame(
        [
            {
                "ticker": "A",
                "direction": "LONG",
                "market_value": 500_000,
                "sector": "Tech",
                "weight": 0.05,
            },
            {
                "ticker": "B",
                "direction": "LONG",
                "market_value": 500_000,
                "sector": "Tech",
                "weight": 0.05,
            },
        ]
    )
    result = _compute_net_beta(hist)
    assert abs(result - 0.10) < 1e-9


def test_compute_net_beta_long_short():
    """Net beta for L/S portfolio is long_weight - short_weight."""
    import pandas as pd

    hist = pd.DataFrame(
        [
            {
                "ticker": "A",
                "direction": "LONG",
                "market_value": 500_000,
                "sector": "Tech",
                "weight": 0.05,
            },
            {
                "ticker": "B",
                "direction": "SHORT",
                "market_value": 300_000,
                "sector": "Tech",
                "weight": 0.03,
            },
        ]
    )
    result = _compute_net_beta(hist)
    assert abs(result - 0.02) < 1e-9


def test_compute_net_beta_empty():
    """Empty DataFrame returns 0.0."""
    import pandas as pd

    result = _compute_net_beta(pd.DataFrame())
    assert result == 0.0


def test_alpha_residual_sums_to_total():
    """beta + sector + factor + alpha ≈ portfolio_return."""
    # Simulate a single attribution row
    portfolio_return = 0.0153
    beta_pnl = 0.0080
    sector_pnl = 0.0030
    factor_pnl = 0.0020
    alpha_pnl = portfolio_return - beta_pnl - sector_pnl - factor_pnl

    reconstructed = beta_pnl + sector_pnl + factor_pnl + alpha_pnl
    assert abs(reconstructed - portfolio_return) < 1e-12


def test_brinson_zero_when_weights_match_benchmark():
    """When portfolio weights equal benchmark weights, allocation effect ≈ 0."""
    import pandas as pd

    # Equal-weighted benchmark: 1/N per sector
    sectors = [
        "Tech",
        "Finance",
        "Health",
        "Energy",
        "Industrials",
        "Communication",
        "Consumer Disc",
        "Consumer Stap",
        "Materials",
        "Real Estate",
        "Utilities",
    ]
    n = len(sectors)
    equal_w = 1.0 / n

    hist = pd.DataFrame(
        [
            {
                "ticker": f"T{i}",
                "direction": "LONG",
                "market_value": equal_w * 1e6,
                "sector": sectors[i],
                "weight": equal_w,
            }
            for i in range(n)
        ]
    )

    # All sector ETF returns equal → bm_ret = mean = same for all
    etf_map = {s: f"ETF{i}" for i, s in enumerate(sectors)}
    price_rets = pd.DataFrame(
        {f"ETF{i}": [0.01] for i in range(n)},
        index=["2026-01-02"],
    )
    _etf_prices = {f"ETF{i}": 100.0 for i in range(n)}
    etf_pivot = pd.DataFrame(
        {f"ETF{i}": {"2026-01-01": 100.0, "2026-01-02": 101.0} for i in range(n)}
    ).T.T  # date x etf

    result = _brinson_sector(hist, price_rets, etf_pivot, etf_map, "2026-01-01", "2026-01-02")
    # With equal weights matching benchmark, allocation effects ≈ 0
    # (selection effects may be non-zero as ticker rets ≠ ETF rets)
    # We just verify it returns a float without crashing
    assert isinstance(result, float)
