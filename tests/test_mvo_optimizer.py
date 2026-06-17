"""Tests for portfolio/mvo_optimizer.py."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import portfolio.db  # noqa: F401
from data.db import daily_prices, get_engine, initialise_schema
from portfolio.mvo_optimizer import optimise


@pytest.fixture
def tmp_engine(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path / 'test.db'}")
    initialise_schema(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def tmp_db(tmp_engine):
    conn = tmp_engine.connect()
    yield conn
    conn.close()


def _make_candidates(n_long=5, n_short=5):
    longs = pd.DataFrame(
        {
            "ticker": [f"L{i}" for i in range(n_long)],
            "direction": "LONG",
            "combined_score": [90 - i * 5 for i in range(n_long)],
            "sector": "Technology",
        }
    )
    shorts = pd.DataFrame(
        {
            "ticker": [f"S{i}" for i in range(n_short)],
            "direction": "SHORT",
            "combined_score": [10 + i * 5 for i in range(n_short)],
            "sector": "Technology",
        }
    )
    return pd.concat([longs, shorts], ignore_index=True)


def _seed_prices(conn, tickers, n_days=90, base_price=100.0, seed=42):
    rng = np.random.default_rng(seed)
    for ticker in tickers:
        price = base_price
        for i in range(n_days):
            d = f"2024-{(i // 30) + 1:02d}-{(i % 30) + 1:02d}"
            ret = rng.normal(0.0005, 0.015)
            price = max(price * (1 + ret), 1.0)
            conn.execute(
                daily_prices.insert().values(
                    ticker=ticker,
                    date=d,
                    open=price,
                    high=price * 1.01,
                    low=price * 0.99,
                    close=price,
                    adj_close=price,
                    volume=500_000,
                )
            )
    conn.commit()


def _make_prices_dict(tickers, n_days=25):
    rows = []
    for _ in range(n_days):
        rows.append(
            {"close": 100.0, "high": 101.0, "low": 99.0, "adj_close": 100.0, "volume": 1_000_000}
        )
    df = pd.DataFrame(rows)
    return {t: df.copy() for t in tickers}


_CONFIG = {
    "portfolio": {
        "num_longs": 5,
        "num_shorts": 5,
        "target_long_gross": 0.90,
        "target_short_gross": 0.60,
        "max_position_pct": 0.30,
        "min_position_pct": 0.01,
        "max_sector_pct": 0.90,
        "max_sector_net_pct": 0.90,
        "max_beta": 0.99,
        "nav_usd": 1_000_000,
        "adv_lookback_days": 10,
        "adv_max_pct": 1.0,
        "earnings_blackout_days": 5,
        "conviction_tilt": {"top5_multiplier": 1.5, "top10_multiplier": 1.25},
        "mvo": {
            "risk_aversion": 1.0,
            "cov_lookback_days": 60,
            "score_to_return_map": {"score_100": 0.15, "score_0": -0.15},
            "max_iter": 500,
        },
        "transaction_costs": {"spread_hl_fraction": 0.05, "market_impact_coef": 0.10},
    }
}


class TestMvoOptimiser:
    def test_returns_dataframe_with_expected_columns(self, tmp_db):
        cands = _make_candidates()
        tickers = cands["ticker"].tolist()
        _seed_prices(tmp_db, tickers + ["SPY"])
        prices = _make_prices_dict(tickers)
        betas = pd.Series(dict.fromkeys(tickers, 1.0))

        result = optimise(cands, prices, betas, _CONFIG, "2024-03-30", tmp_db)
        assert not result.empty
        for col in ["ticker", "direction", "weight", "shares"]:
            assert col in result.columns

    def test_long_weights_sum_to_gross(self, tmp_db):
        cands = _make_candidates()
        tickers = cands["ticker"].tolist()
        _seed_prices(tmp_db, tickers + ["SPY"])
        prices = _make_prices_dict(tickers)
        betas = pd.Series(dict.fromkeys(tickers, 1.0))

        result = optimise(cands, prices, betas, _CONFIG, "2024-03-30", tmp_db)
        long_sum = result[result["direction"] == "LONG"]["weight"].sum()
        assert long_sum == pytest.approx(0.90, abs=0.05)

    def test_short_weights_sum_to_gross(self, tmp_db):
        cands = _make_candidates()
        tickers = cands["ticker"].tolist()
        _seed_prices(tmp_db, tickers + ["SPY"])
        prices = _make_prices_dict(tickers)
        betas = pd.Series(dict.fromkeys(tickers, 1.0))

        result = optimise(cands, prices, betas, _CONFIG, "2024-03-30", tmp_db)
        # Short weights are negative
        short_sum = result[result["direction"] == "SHORT"]["weight"].abs().sum()
        assert short_sum == pytest.approx(0.60, abs=0.05)

    def test_falls_back_to_conviction_tilt_on_insufficient_data(self, tmp_db):
        # No price data → covariance fails → fallback
        cands = _make_candidates()
        tickers = cands["ticker"].tolist()
        prices = _make_prices_dict(tickers)
        betas = pd.Series(dict.fromkeys(tickers, 1.0))

        # Should not raise; should return valid result via fallback
        result = optimise(cands, prices, betas, _CONFIG, "2024-03-30", tmp_db)
        assert not result.empty

    def test_all_weights_within_bounds(self, tmp_db):
        cands = _make_candidates()
        tickers = cands["ticker"].tolist()
        _seed_prices(tmp_db, tickers + ["SPY"])
        prices = _make_prices_dict(tickers)
        betas = pd.Series(dict.fromkeys(tickers, 1.0))

        result = optimise(cands, prices, betas, _CONFIG, "2024-03-30", tmp_db)
        max_pos = _CONFIG["portfolio"]["max_position_pct"]
        assert (result["weight"].abs() <= max_pos + 1e-4).all()
