"""Packet 66: both optimisers plan a sector's combined long + short gross within the one shared
sector limit (`risk.pre_trade.max_sector_pct`), and the plan passes the pre-trade sector check.

The candidate set below is the 2026-11-12 shape: before this packet the conviction-tilt
optimiser planned Industrials at 27.3% combined (per side and net caps all met), which the
pre-trade check rejects above 25%.
"""

import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).parent.parent))

import analysis.db  # noqa: F401
import factors.db  # noqa: F401
import portfolio.db  # noqa: F401
import risk.db  # noqa: F401
from data.db import daily_prices, initialise_schema
from factors.db import factor_scores
from portfolio import mvo_optimizer, optimizer
from portfolio.db import portfolio_positions
from portfolio.rebalance import _cap_sector_shares, generate_trades
from portfolio.state import load_positions
from risk.pre_trade import run_pre_trade

_SCORE_DATE = "2026-11-12"
_NAV = 100_000.0
_CAP = 0.25
_OTHER = [
    "Materials",
    "Energy",
    "Financials",
    "Health Care",
    "Utilities",
    "Real Estate",
    "Consumer Staples",
    "Information Technology",
]
_IND_LONG = {0, 1, 10}  # side ranks of the Industrials longs
_IND_SHORT = {9, 10, 11, 12}
# Broker book: Industrials held at 26.9% combined ($100 a share, NAV $100k)
_HELD = {"L00": 50, "L01": 50, "L10": 40, "S09": -30, "S10": -30, "S11": -30, "S12": -39}


@pytest.fixture
def conn():
    engine = sa.create_engine("sqlite:///:memory:", future=True)
    initialise_schema(engine)
    with engine.connect() as c:
        yield c
    engine.dispose()


def _candidates(n=20, other=_OTHER, ind_long=_IND_LONG, ind_short=_IND_SHORT):
    rows = []
    for side, ind, base in (("LONG", ind_long, 95.0), ("SHORT", ind_short, 5.0)):
        for i in range(n):
            rows.append(
                {
                    "ticker": f"{side[0]}{i:02d}",
                    "direction": side,
                    "combined_score": base - i if side == "LONG" else base + i,
                    "sector": "Industrials" if i in ind else other[i % len(other)],
                }
            )
    return pd.DataFrame(rows)


def _config(sector_cap=_CAP, max_beta=0.15):
    return {
        "portfolio": {
            "num_longs": 20,
            "num_shorts": 20,
            "target_long_gross": 0.80,
            "target_short_gross": 0.70,
            "max_position_pct": 0.05,
            "min_position_pct": 0.005,
            "max_sector_pct": 0.25,
            "max_sector_net_pct": 0.05,
            "max_beta": max_beta,
            "nav_usd": _NAV,
            "adv_max_pct": 1.0,
            "adv_lookback_days": 20,
            "earnings_blackout_days": 5,
            # The trimmed-reduction case is deliberate decision 2 (spec 66), not tested here
            "turnover_budget_pct": 10.0,
            "conviction_tilt": {"top5_multiplier": 1.5, "top10_multiplier": 1.25},
            "mvo": {
                "risk_aversion": 1.0,
                "cov_lookback_days": 60,
                "score_to_return_map": {"score_100": 0.15, "score_0": -0.15},
                "max_iter": 500,
            },
            "transaction_costs": {"spread_hl_fraction": 0.05, "market_impact_coef": 0.10},
        },
        "risk": {
            "pre_trade": {
                "adv_lookback": 20,
                "adv_pct": 1000.0,
                "max_position_pct": 0.05,
                "max_sector_pct": sector_cap,
                "max_gross": 5.0,
                "net_min": -5.0,
                "net_max": 5.0,
                "max_net_beta": 50.0,
                "max_pairwise_corr": 1.01,
                "corr_lookback": 60,
                "earnings_blackout_days": 5,
            }
        },
    }


def _sector_gross(target: pd.DataFrame) -> pd.Series:
    return target.assign(a=target["weight"].abs()).groupby("sector")["a"].sum()


def _book_gross(target: pd.DataFrame, direction: str) -> float:
    return float(target.loc[target["direction"] == direction, "weight"].abs().sum())


def _flat_betas(cands):
    return pd.Series(1.0, index=cands["ticker"])


def _seed_prices(conn, tickers, n_days=90, seed=7):
    """Random walks ending at exactly $100 so share counts match the weights."""
    rng = np.random.default_rng(seed)
    start = date.fromisoformat(_SCORE_DATE) - timedelta(days=n_days - 1)
    rows = []
    for ticker in tickers:
        path = np.cumprod(1 + rng.normal(0.0, 0.015, n_days))
        path = path / path[-1] * 100.0
        for i, p in enumerate(path):
            rows.append(
                {
                    "ticker": ticker,
                    "date": str(start + timedelta(days=i)),
                    "open": p,
                    "high": p * 1.01,
                    "low": p * 0.99,
                    "close": p,
                    "adj_close": p,
                    "volume": 1_000_000,
                }
            )
    conn.execute(daily_prices.insert(), rows)
    conn.commit()
    df = pd.DataFrame(rows)
    return {t: g.drop(columns="ticker").reset_index(drop=True) for t, g in df.groupby("ticker")}


class TestConviction:
    def test_sector_gross_capped(self, conn):
        target = optimizer.optimise(
            _candidates(), {}, _flat_betas(_candidates()), _config(), _SCORE_DATE, conn
        )
        gross = _sector_gross(target)
        assert gross["Industrials"] <= _CAP + 1e-9
        assert (gross <= _CAP + 1e-9).all()

    def test_gross_targets_kept(self, conn):
        target = optimizer.optimise(
            _candidates(), {}, _flat_betas(_candidates()), _config(), _SCORE_DATE, conn
        )
        assert _book_gross(target, "LONG") == pytest.approx(0.80)
        assert _book_gross(target, "SHORT") == pytest.approx(0.70)

    def test_positions_within_cap(self, conn):
        target = optimizer.optimise(
            _candidates(), {}, _flat_betas(_candidates()), _config(), _SCORE_DATE, conn
        )
        # Refilled names are bounded by the position cap; capped-sector names only shrink
        assert target["weight"].abs().max() <= 0.05 + 1e-9

    def test_beta_tilt_cannot_reopen_breach(self, conn):
        """Low-beta Utilities longs gain weight in the beta tilt; the sector stays capped."""
        cands = _candidates()
        betas = _flat_betas(cands).copy()
        utilities_longs = cands[(cands["sector"] == "Utilities") & (cands["direction"] == "LONG")]
        betas[utilities_longs["ticker"]] = 0.1
        betas[cands.loc[cands["direction"] == "LONG", "ticker"].iloc[2:6]] = 2.5
        cfg = _config(max_beta=0.02)
        target = optimizer.optimise(cands, {}, betas, cfg, _SCORE_DATE, conn)
        assert (_sector_gross(target) <= _CAP + 1e-9).all()

    def test_too_few_sectors_short_of_gross_with_warning(self, conn, caplog):
        caplog.set_level(logging.WARNING, logger="portfolio.optimizer")
        cands = _candidates(other=_OTHER[:4], ind_long=set(), ind_short=set())
        # 4 sectors x 0.25 = 1.00 of room for 1.50 of gross
        target = optimizer.optimise(cands, {}, _flat_betas(cands), _config(), _SCORE_DATE, conn)
        assert (_sector_gross(target) <= _CAP + 1e-9).all()
        assert target["weight"].abs().sum() < 1.50 - 1e-6
        assert any("below its gross target" in r.getMessage() for r in caplog.records)


class TestMvo:
    def test_sector_gross_capped(self, conn):
        cands = _candidates()
        prices = _seed_prices(conn, cands["ticker"].tolist())
        target = mvo_optimizer._mvo(cands, prices, _flat_betas(cands), _config(), _SCORE_DATE, conn)
        assert (_sector_gross(target) <= _CAP + 1e-6).all()

    def test_infeasible_falls_back_to_conviction(self, conn, caplog):
        caplog.set_level(logging.WARNING)
        cands = _candidates(other=_OTHER[:4], ind_long=set(), ind_short=set())
        prices = _seed_prices(conn, cands["ticker"].tolist())
        target = mvo_optimizer.optimise(
            cands, prices, _flat_betas(cands), _config(), _SCORE_DATE, conn
        )
        assert any("falling back to conviction-tilt" in r.getMessage() for r in caplog.records)
        assert (_sector_gross(target) <= _CAP + 1e-9).all()


class TestSectorShares:
    _PRICES = {"A": 97.0, "B": 113.0, "C": 41.0}

    def test_rounding_over_limit_trimmed(self):
        # Planned at 24.97%; rounding to 86 / 74 / -204 shares lands at 25.07%
        raw = {"A": 85.6, "B": 73.6, "C": -203.6}
        rounded = {t: float(round(s)) for t, s in raw.items()}
        before = sum(abs(rounded[t]) * self._PRICES[t] for t in raw) / _NAV
        assert before > _CAP
        out = _cap_sector_shares(rounded, raw, self._PRICES, _NAV, _CAP)
        assert sum(abs(out[t]) * self._PRICES[t] for t in out) / _NAV <= _CAP
        assert out["C"] < 0
        assert all(abs(out[t]) <= abs(rounded[t]) for t in out)

    def test_under_limit_unchanged(self):
        raw = {"A": 50.4, "B": -30.6}
        rounded = {"A": 50.0, "B": -31.0}
        assert _cap_sector_shares(rounded, raw, self._PRICES, _NAV, _CAP) == rounded

    def test_no_sector_not_capped(self):
        target = pd.DataFrame(
            [
                {"ticker": t, "shares": 150.0, "direction": "LONG", "combined_score": 80.0}
                for t in ("A", "B")
            ]
        )
        prices = {t: pd.DataFrame({"close": [100.0] * 5, "volume": [1e6] * 5}) for t in "AB"}
        cfg = {"portfolio": {"nav_usd": _NAV, "max_position_pct": 1.0}}
        cfg["risk"] = {"pre_trade": {"max_position_pct": 1.0}}
        out = generate_trades(
            pd.DataFrame(columns=["ticker", "shares"]), target, prices, cfg, None, _SCORE_DATE,
            commit=False,
        )  # fmt: skip
        assert set(out["target_shares"]) == {150}


def _seed_book(conn, cands):
    prices = _seed_prices(conn, cands["ticker"].tolist())
    for row in cands.itertuples():
        conn.execute(
            factor_scores.insert().values(
                ticker=row.ticker, score_date=_SCORE_DATE, sector=row.sector
            )
        )
    for ticker, shares in _HELD.items():
        conn.execute(
            portfolio_positions.insert().values(
                ticker=ticker,
                direction="LONG" if shares > 0 else "SHORT",
                shares=float(shares),
                current_price=100.0,
                market_value=shares * 100.0,
                weight=shares * 100.0 / _NAV,
            )
        )
    conn.commit()
    return prices


class TestPipeline:
    @pytest.mark.parametrize("module", [optimizer, mvo_optimizer], ids=["conviction", "mvo"])
    def test_plan_passes_pre_trade_sector_check(self, conn, tmp_path, module):
        cands = _candidates()
        prices = _seed_book(conn, cands)
        held_gross = sum(abs(s) for s in _HELD.values()) * 100.0 / _NAV
        assert held_gross == pytest.approx(0.269)
        cfg = _config()
        current = load_positions(conn)
        target = module.optimise(cands, prices, _flat_betas(cands), cfg, _SCORE_DATE, conn, current)
        assert _sector_gross(target)["Industrials"] <= _CAP + 1e-6
        generate_trades(current, target, prices, cfg, conn, _SCORE_DATE, commit=True)
        res = run_pre_trade(conn, _SCORE_DATE, cfg, tmp_path, whatif=True)
        sector_rejects = res[res["reason"].str.startswith("SECTOR_CONCENTRATION")]
        assert sector_rejects.empty, sector_rejects.to_dict("records")
        assert (res["result"] == "APPROVED").all(), res[res["result"] != "APPROVED"]


class TestSharedLimit:
    def test_one_value_moves_plan_and_check(self, conn, tmp_path):
        """risk.pre_trade.max_sector_pct = 0.20 tightens the plan and the check together."""
        cands = _candidates()
        prices = _seed_book(conn, cands)
        current = load_positions(conn)
        cfg = _config(sector_cap=0.20)
        target = optimizer.optimise(cands, prices, _flat_betas(cands), cfg, _SCORE_DATE, conn)
        gross = _sector_gross(target)
        assert (gross <= 0.20 + 1e-9).all()
        assert gross.max() == pytest.approx(0.20)
        generate_trades(current, target, prices, cfg, conn, _SCORE_DATE, commit=True)
        res = run_pre_trade(conn, _SCORE_DATE, cfg, tmp_path, whatif=True)
        assert not res["reason"].str.startswith("SECTOR_CONCENTRATION").any()
        # The same plan checked at 0.15 (the check reads the same key) has sector rejects
        res = run_pre_trade(conn, _SCORE_DATE, _config(sector_cap=0.15), tmp_path, whatif=True)
        assert res["reason"].str.startswith("SECTOR_CONCENTRATION").any()
