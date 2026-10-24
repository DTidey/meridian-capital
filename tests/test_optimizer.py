"""Tests for portfolio/optimizer.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from datetime import date, timedelta

import pandas as pd
import pytest

import portfolio.db  # noqa: F401
from data.db import earnings_calendar
from portfolio.optimizer import _apply_conviction_tilt, optimise

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CONFIG = {
    "portfolio": {
        "nav_usd": 1_000_000,
        "num_longs": 10,
        "num_shorts": 10,
        "target_long_gross": 0.80,
        "target_short_gross": 0.70,
        # max_position_pct is set generously so that equal-weight positions (0.09
        # each) clear the cap.  Tests that specifically verify the cap use a
        # dedicated config where the cap is tight enough to bind.
        "max_position_pct": 0.15,
        "min_position_pct": 0.005,
        "max_sector_pct": 0.25,
        "max_sector_net_pct": 0.05,
        "max_beta": 0.30,
        "adv_max_pct": 1.0,  # disable liquidity cap in most tests
        "adv_lookback_days": 20,
        "earnings_blackout_days": 5,
        "conviction_tilt": {
            "top5_multiplier": 1.50,
            "top10_multiplier": 1.25,
        },
    }
}

_SCORE_DATE = "2026-03-01"


def _make_price_df(n=25, close=100.0, high=101.0, low=99.0, volume=1_000_000):
    return pd.DataFrame(
        {
            "close": [float(close)] * n,
            "high": [float(high)] * n,
            "low": [float(low)] * n,
            "volume": [float(volume)] * n,
        }
    )


def _build_candidates():
    """20 LONG candidates (score 51–100 odd) + 20 SHORT candidates (score 1–40 even)."""
    rows = []
    # LONGs: scores 51, 53, 55, … 89  (20 values)
    for i, score in enumerate(range(51, 100, 2)[:20]):
        rows.append(
            {
                "ticker": f"L{i:02d}",
                "direction": "LONG",
                "combined_score": float(score),
                "sector": f"Sector{i % 5}",
            }
        )
    # SHORTs: scores 2, 4, 6, … 40  (20 values)
    for i, score in enumerate(range(2, 42, 2)[:20]):
        rows.append(
            {
                "ticker": f"S{i:02d}",
                "direction": "SHORT",
                "combined_score": float(score),
                "sector": f"Sector{i % 5}",
            }
        )
    return pd.DataFrame(rows)


def _build_betas(candidates):
    return pd.Series(1.0, index=candidates["ticker"])


def _build_prices(candidates):
    return {row["ticker"]: _make_price_df() for _, row in candidates.iterrows()}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOptimizerWeights:
    def test_long_weights_sum_to_target(self, tmp_db):
        candidates = _build_candidates()
        betas = _build_betas(candidates)
        prices = _build_prices(candidates)
        result = optimise(candidates, prices, betas, _CONFIG, _SCORE_DATE, tmp_db)
        longs = result[result["direction"] == "LONG"]
        assert longs["weight"].abs().sum() == pytest.approx(0.80, abs=0.02)

    def test_short_weights_sum_to_target(self, tmp_db):
        candidates = _build_candidates()
        betas = _build_betas(candidates)
        prices = _build_prices(candidates)
        result = optimise(candidates, prices, betas, _CONFIG, _SCORE_DATE, tmp_db)
        shorts = result[result["direction"] == "SHORT"]
        assert shorts["weight"].abs().sum() == pytest.approx(0.70, abs=0.02)

    def test_no_position_exceeds_max(self, tmp_db):
        """With 20 LONG and 20 SHORT candidates (all selected), equal-weight is
        0.045 per LONG.  Conviction tilt boosts the #1 position by 1.5× → 0.067.
        A max_position_pct of 0.10 must not be breached."""
        # Use 20-position books so the cap is meaningful but not clamped away by renorm.
        cfg = dict(_CONFIG["portfolio"])
        cfg.update(
            {
                "num_longs": 20,
                "num_shorts": 20,
                "max_position_pct": 0.10,
                "max_beta": 1.0,
                "max_sector_pct": 1.0,
                "max_sector_net_pct": 1.0,
            }
        )
        config = {"portfolio": cfg}
        candidates = _build_candidates()
        betas = _build_betas(candidates)
        prices = _build_prices(candidates)
        result = optimise(candidates, prices, betas, config, _SCORE_DATE, tmp_db)
        max_pos = cfg["max_position_pct"]
        assert (result["weight"].abs() <= max_pos + 1e-6).all()


class TestOptimizerSelection:
    def test_selects_top_n_longs(self, tmp_db):
        """Result LONGs should come from the 10 highest-scored LONG candidates."""
        candidates = _build_candidates()
        betas = _build_betas(candidates)
        prices = _build_prices(candidates)
        result = optimise(candidates, prices, betas, _CONFIG, _SCORE_DATE, tmp_db)

        long_cands = (
            candidates[candidates["direction"] == "LONG"]
            .sort_values("combined_score", ascending=False)
            .head(10)["ticker"]
            .tolist()
        )
        result_longs = result[result["direction"] == "LONG"]["ticker"].tolist()
        assert set(result_longs) == set(long_cands)

    def test_selects_bottom_n_shorts(self, tmp_db):
        """Result SHORTs should come from the 10 lowest-scored SHORT candidates."""
        candidates = _build_candidates()
        betas = _build_betas(candidates)
        prices = _build_prices(candidates)
        result = optimise(candidates, prices, betas, _CONFIG, _SCORE_DATE, tmp_db)

        short_cands = (
            candidates[candidates["direction"] == "SHORT"]
            .sort_values("combined_score", ascending=True)
            .head(10)["ticker"]
            .tolist()
        )
        result_shorts = result[result["direction"] == "SHORT"]["ticker"].tolist()
        assert set(result_shorts) == set(short_cands)


def _book(scores):
    return pd.DataFrame(
        {
            "ticker": [f"T{i:02d}" for i in range(len(scores))],
            "combined_score": [float(s) for s in scores],
            "weight": 0.0,
        }
    )


def _loose_config():
    """20-name books with sector, beta and position caps that never bind."""
    cfg = dict(_CONFIG["portfolio"])
    cfg.update(
        {
            "num_longs": 20,
            "num_shorts": 20,
            "max_position_pct": 1.0,
            "max_beta": 1.0,
            "max_sector_pct": 1.0,
            "max_sector_net_pct": 1.0,
        }
    )
    return {"portfolio": cfg}


class TestConvictionTilt:
    def test_strongest_short_gets_top_multiplier(self):
        # 20 shorts: top 5% = 1 name at 1.5x, next 5% = 1 name at 1.25x.
        book = _book(range(1, 21))
        tilted = _apply_conviction_tilt(book, 1.5, 1.25, 0.60, ascending=True)
        w = tilted.set_index("ticker")["weight"]
        base = w["T10"]
        assert w["T00"] == pytest.approx(1.5 * base)
        assert w["T01"] == pytest.approx(1.25 * base)

    def test_weakest_short_gets_base_weight(self):
        book = _book(range(1, 21))
        tilted = _apply_conviction_tilt(book, 1.5, 1.25, 0.60, ascending=True)
        w = tilted.set_index("ticker")["weight"]
        assert w["T19"] == pytest.approx(w["T10"])
        assert w["T19"] < w["T00"]

    def test_strongest_long_gets_top_multiplier(self):
        book = _book(range(81, 101))
        tilted = _apply_conviction_tilt(book, 1.5, 1.25, 0.90)
        w = tilted.set_index("ticker")["weight"]
        assert w["T19"] == pytest.approx(1.5 * w["T05"])
        assert w["T18"] == pytest.approx(1.25 * w["T05"])
        assert w["T00"] == pytest.approx(w["T05"])

    def test_tilted_books_sum_to_gross(self):
        shorts = _apply_conviction_tilt(_book(range(1, 21)), 1.5, 1.25, 0.60, ascending=True)
        longs = _apply_conviction_tilt(_book(range(81, 101)), 1.5, 1.25, 0.90)
        assert shorts["weight"].sum() == pytest.approx(0.60)
        assert longs["weight"].sum() == pytest.approx(0.90)

    def test_optimise_tilts_strongest_short(self, tmp_db):
        candidates = _build_candidates()
        result = optimise(
            candidates,
            _build_prices(candidates),
            _build_betas(candidates),
            _loose_config(),
            _SCORE_DATE,
            tmp_db,
        )
        w = result.set_index("ticker")["weight"].abs()
        # S00 (score 2) is the strongest short, S19 (score 40) the weakest.
        assert w["S00"] > w["S19"]
        # Adversarial: the long book keeps rewarding its highest score (L19, 89).
        assert w["L19"] > w["L00"]

    def test_unsorted_input_short_book(self):
        # Adversarial: input order must not matter; the lowest score still wins.
        book = _book([12, 3, 19, 7, 1, 15, 9, 20, 5, 11, 2, 17, 4, 14, 6, 18, 8, 13, 10, 16])
        tilted = _apply_conviction_tilt(book, 1.5, 1.25, 0.60, ascending=True)
        top = tilted.sort_values("weight", ascending=False)["combined_score"].head(2).tolist()
        assert top == [1.0, 2.0]

    def test_single_short(self):
        # Adversarial: one name takes the whole gross target whatever the multiplier.
        tilted = _apply_conviction_tilt(_book([7]), 1.5, 1.25, 0.60, ascending=True)
        assert tilted["weight"].tolist() == pytest.approx([0.60])

    def test_empty_book_unchanged(self):
        empty = _book([])
        assert _apply_conviction_tilt(empty, 1.5, 1.25, 0.60, ascending=True).empty


class TestEarningsHaircut:
    def test_earnings_haircut_reduces_weight(self, tmp_db):
        """LONG ticker with earnings within blackout window gets weight below the
        mean of its non-haircutted peers after renormalisation.

        Requires max_position_pct large enough (0.15) that conviction-tilt weights
        are not uniformly clamped back to equal weight before the haircut runs.
        """
        candidates = _build_candidates()
        betas = _build_betas(candidates)
        prices = _build_prices(candidates)

        # Pick the top-scoring LONG and put its earnings inside the blackout window
        top_long = (
            candidates[candidates["direction"] == "LONG"]
            .sort_values("combined_score", ascending=False)
            .iloc[0]["ticker"]
        )
        earn_date = (date.fromisoformat(_SCORE_DATE) + timedelta(days=2)).isoformat()
        tmp_db.execute(
            earnings_calendar.insert().values(
                ticker=top_long,
                earnings_date=earn_date,
                eps_estimate=1.0,
                fetched_at="2026-01-01T00:00:00+00:00",
            )
        )
        tmp_db.commit()

        result = optimise(candidates, prices, betas, _CONFIG, _SCORE_DATE, tmp_db)

        longs = result[result["direction"] == "LONG"].set_index("ticker")
        haircutted_weight = abs(longs.loc[top_long, "weight"])
        others_mean = longs.drop(index=top_long)["weight"].abs().mean()
        assert haircutted_weight < others_mean


class TestOptimizerEdgeCases:
    def test_returns_empty_when_no_candidates(self, tmp_db):
        candidates = pd.DataFrame(columns=["ticker", "direction", "combined_score", "sector"])
        betas = pd.Series(dtype=float)
        prices: dict = {}
        result = optimise(candidates, prices, betas, _CONFIG, _SCORE_DATE, tmp_db)
        assert result.empty
