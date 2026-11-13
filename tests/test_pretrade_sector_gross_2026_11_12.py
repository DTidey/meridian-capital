"""Packet 66: per-trade sector check in Layer 5, Layer 4 targets capped at the position limit.

Reproduces the 2026-11-12 run: Industrials pro-forma gross over 25% rejected every opening
trade (MOS, COP, BEN included), and Layer 4 targets above 5% (CF, DVA, AIZ) were rejected by
the pre-trade POSITION_SIZE check.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import pytest
import sqlalchemy as sa

import analysis.db  # noqa: F401
import factors.db  # noqa: F401
import portfolio.db  # noqa: F401
import risk.db  # noqa: F401
from data.db import daily_prices, initialise_schema
from factors.db import factor_scores
from portfolio.db import portfolio_positions, position_approvals
from portfolio.rebalance import _cap_shares, generate_trades
from risk.pre_trade import run_pre_trade

_SCORE_DATE = "2026-11-12"
_NAV = 100_000.0
_PRICE = 100.0  # 1 share = 0.1% of NAV


@pytest.fixture
def conn():
    engine = sa.create_engine("sqlite:///:memory:", future=True)
    initialise_schema(engine)
    with engine.connect() as c:
        yield c
    engine.dispose()


def _config(max_sector=0.25, max_position=0.05, portfolio_max_position=0.05):
    """Production limits for sector and position; the other checks are opened up."""
    return {
        "portfolio": {"nav_usd": _NAV, "max_position_pct": portfolio_max_position},
        "risk": {
            "pre_trade": {
                "adv_lookback": 20,
                "adv_pct": 1000.0,
                "max_position_pct": max_position,
                "max_sector_pct": max_sector,
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


def _price_rows(ticker, price=_PRICE, n=30):
    start = date.fromisoformat(_SCORE_DATE) - timedelta(days=n - 1)
    return [
        {
            "ticker": ticker,
            "date": str(start + timedelta(days=i)),
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "adj_close": price,
            "volume": 1_000_000,
        }
        for i in range(n)
    ]


def _seed_names(conn, sectors: dict[str, str], prices: dict[str, float] | None = None):
    prices = prices or {}
    rows = []
    for ticker, sector in sectors.items():
        rows += _price_rows(ticker, prices.get(ticker, _PRICE))
        conn.execute(
            factor_scores.insert().values(ticker=ticker, score_date=_SCORE_DATE, sector=sector)
        )
    conn.execute(daily_prices.insert(), rows)
    conn.commit()


def _hold(conn, ticker, shares, price=_PRICE):
    conn.execute(
        portfolio_positions.insert().values(
            ticker=ticker,
            direction="LONG" if shares > 0 else "SHORT",
            shares=float(shares),
            current_price=price,
            market_value=shares * price,
            weight=shares * price / _NAV,
        )
    )
    conn.commit()


def _pending(conn, ticker, action, current, target):
    conn.execute(
        position_approvals.insert().values(
            rebalance_date=_SCORE_DATE,
            ticker=ticker,
            action=action,
            target_shares=float(target),
            current_shares=float(current),
            delta_shares=float(target - current),
            status="PENDING",
            created_at="2026-11-12T22:00:00",
        )
    )
    conn.commit()


def _results(conn, config):
    df = run_pre_trade(conn, _SCORE_DATE, config, Path("/nonexistent-cache"), whatif=True)
    return {r.ticker: (r.result, r.reason) for r in df.itertuples()}


# Industrials held at 28.0% gross: 3 longs and 2 shorts of 5%, one long of 3%.
_INDUSTRIALS_HELD = {"GE": 50, "HON": 50, "CAT": 50, "UPS": -50, "BA": -50, "LMT": 30}
_SECTORS = {
    **dict.fromkeys(_INDUSTRIALS_HELD, "Industrials"),
    "DE": "Industrials",
    "MOS": "Materials",
    "COP": "Energy",
    "BEN": "Financials",
}


def _seed_breaching_book(conn):
    _seed_names(conn, _SECTORS)
    for ticker, shares in _INDUSTRIALS_HELD.items():
        _hold(conn, ticker, shares)


class TestSectorCheck:
    def test_other_sector_trade_passes(self, conn):
        _seed_breaching_book(conn)
        _pending(conn, "MOS", "BUY", 0, 40)
        res = _results(conn, _config())
        assert res["MOS"] == ("APPROVED", "All checks passed")

    def test_reducing_flip_in_breaching_sector_passes(self, conn):
        _seed_breaching_book(conn)
        _pending(conn, "HON", "SHORT", 50, -30)  # 5% long -> 3% short: gross -2%, still 26%
        res = _results(conn, _config())
        assert res["HON"][0] == "APPROVED"

    def test_adding_trade_in_breaching_sector_rejected(self, conn):
        _seed_breaching_book(conn)
        _pending(conn, "DE", "BUY", 0, 19)
        res = _results(conn, _config())
        assert res["DE"] == (
            "REJECTED",
            "SECTOR_CONCENTRATION: Industrials pro-forma gross 29.9% > max 25.0%",
        )

    def test_larger_flip_in_breaching_sector_rejected(self, conn):
        _seed_breaching_book(conn)
        _pending(conn, "LMT", "SHORT", 30, -40)  # 3% long -> 4% short adds 1% gross
        res = _results(conn, _config())
        assert res["LMT"][0] == "REJECTED"
        assert "SECTOR_CONCENTRATION: Industrials" in res["LMT"][1]

    def test_trade_taking_own_sector_over_rejected(self, conn):
        _seed_names(conn, {"XOM": "Energy", "CVX": "Energy", "COP": "Energy"})
        _hold(conn, "XOM", 50)
        _hold(conn, "CVX", -50)
        _pending(conn, "COP", "BUY", 0, 20)  # Energy 10% -> 12% under a 11% limit
        res = _results(conn, _config(max_sector=0.11))
        assert res["COP"] == (
            "REJECTED",
            "SECTOR_CONCENTRATION: Energy pro-forma gross 12.0% > max 11.0%",
        )

    def test_add_in_unbreached_sector_passes_beside_breach(self, conn):
        """Adds in a sector under the limit pass even with Industrials adds rejected."""
        _seed_breaching_book(conn)
        _pending(conn, "DE", "BUY", 0, 19)
        _pending(conn, "COP", "SHORT", 0, -40)
        res = _results(conn, _config())
        assert res["DE"][0] == "REJECTED"
        assert res["COP"] == ("APPROVED", "All checks passed")

    def test_trade_without_sector_passes(self, conn):
        _seed_breaching_book(conn)
        conn.execute(daily_prices.insert(), _price_rows("NOSEC"))
        conn.commit()
        _pending(conn, "NOSEC", "BUY", 0, 10)
        res = _results(conn, _config())
        assert res["NOSEC"][0] == "APPROVED"


class TestRegression20261112:
    def test_run_outcome(self, conn):
        _seed_breaching_book(conn)
        _pending(conn, "MOS", "BUY", 0, 40)
        _pending(conn, "COP", "SHORT", 0, -40)
        _pending(conn, "BEN", "BUY", 0, 30)
        _pending(conn, "HON", "SHORT", 50, -30)  # Industrials reduction (flip)
        _pending(conn, "CAT", "SELL", 50, 40)  # Industrials trim
        _pending(conn, "DE", "BUY", 0, 19)  # Industrials add
        res = _results(conn, _config())
        # Pro-forma Industrials: 280 - 20 - 10 + 19 shares = 26.9% of NAV
        assert res["DE"] == (
            "REJECTED",
            "SECTOR_CONCENTRATION: Industrials pro-forma gross 26.9% > max 25.0%",
        )
        for ticker in ("MOS", "COP", "BEN", "HON", "CAT"):
            assert res[ticker][0] == "APPROVED", (ticker, res[ticker])


# ---------------------------------------------------------------------------
# Layer 4 position cap
# ---------------------------------------------------------------------------


def _frames(prices: dict[str, float]) -> dict[str, pd.DataFrame]:
    return {t: pd.DataFrame(_price_rows(t, p)) for t, p in prices.items()}


def _trades(current, target, prices, config, conn=None, commit=False):
    cur = pd.DataFrame(
        [{"ticker": t, "shares": float(s)} for t, s in current.items()],
        columns=["ticker", "shares"],
    )
    tgt = pd.DataFrame(
        [
            {
                "ticker": t,
                "shares": float(s),
                "direction": "LONG" if s > 0 else "SHORT",
                "combined_score": 80.0 if s > 0 else 20.0,
            }
            for t, s in target.items()
        ]
    )
    out = generate_trades(cur, tgt, _frames(prices), config, conn, _SCORE_DATE, commit=commit)
    return out.set_index("ticker")


class TestPositionCap:
    def test_long_and_short_capped(self):
        out = _trades({}, {"CF": 60.0, "DVA": -58.0}, {"CF": 100.0, "DVA": 100.0}, _config())
        assert out.loc["CF", "target_shares"] == 50
        assert out.loc["CF", "action"] == "BUY"
        assert out.loc["CF", "delta_shares"] == 50
        assert out.loc["DVA", "target_shares"] == -50
        assert out.loc["DVA", "action"] == "SHORT"

    def test_rounding_up_capped(self):
        # 5% of 100k at $97 is 51.55 shares: rounds to 52 (5.04%), capped to 51 (4.95%)
        out = _trades({}, {"AIZ": 51.55}, {"AIZ": 97.0}, _config())
        assert out.loc["AIZ", "target_shares"] == 51

    def test_stricter_pre_trade_cap_used(self):
        cfg = _config(max_position=0.04, portfolio_max_position=0.05)
        out = _trades({}, {"CF": 50.0}, {"CF": 100.0}, cfg)
        assert out.loc["CF", "target_shares"] == 40

    def test_held_oversized_trimmed_to_cap(self):
        out = _trades({"CF": 70.0}, {"CF": 65.0}, {"CF": 100.0}, _config())
        assert out.loc["CF", "target_shares"] == 50
        assert out.loc["CF", "action"] == "SELL"
        assert out.loc["CF", "delta_shares"] == -20

    def test_targets_under_cap_unchanged(self):
        out = _trades(
            {"MOS": 10.0}, {"MOS": 40.4, "COP": -49.6}, {"MOS": 100.0, "COP": 100.0}, _config()
        )
        assert out.loc["MOS", "target_shares"] == 40
        assert out.loc["COP", "target_shares"] == -50

    def test_unpriced_not_capped(self):
        out = _trades({}, {"CF": 80.0}, {}, _config())
        assert out.loc["CF", "target_shares"] == 80

    def test_nan_close_not_capped(self):
        out = _trades({}, {"CF": 80.0}, {"CF": float("nan")}, _config())
        assert out.loc["CF", "target_shares"] == 80

    def test_share_dearer_than_cap_goes_to_zero(self):
        out = _trades({}, {"NVR": 0.6}, {"NVR": 7_000.0}, _config())
        assert out.loc["NVR", "target_shares"] == 0
        assert out.loc["NVR", "action"] == "HOLD"

    def test_float_boundary_never_above_cap(self):
        """No price or NAV in a sweep lets the capped target value exceed the cap."""
        cap = 0.05
        for nav in (_NAV, 99_873.21, 1_234_567.89):
            for cents in range(100, 100_000, 3):
                price = cents / 100
                for shares in (10_000_000.0, -10_000_000.0):
                    capped = _cap_shares(shares, price, nav, cap)
                    assert abs(capped) * price / nav <= cap, (nav, price)
                    assert capped * shares >= 0  # sign kept (or zero)

    def test_capped_target_approved_by_pre_trade(self, conn):
        sectors = {"CF": "Materials", "DVA": "Health Care", "AIZ": "Financials"}
        prices = {"CF": 100.0, "DVA": 140.0, "AIZ": 97.0}
        _seed_names(conn, sectors, prices)
        cfg = _config()
        # Weights the optimiser emitted at ~5.05%, plus whole-share rounding
        target = {"CF": 50.6, "DVA": -36.07, "AIZ": 52.06}
        _trades({}, target, prices, cfg, conn=conn, commit=True)
        res = _results(conn, cfg)
        for ticker in sectors:
            assert res[ticker][0] == "APPROVED", (ticker, res[ticker])
