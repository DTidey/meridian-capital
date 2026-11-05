"""Packet 58 regression: the 2026-11-04 trade list survives Layer 5 as Layer 4 planned it.

Rows from docs/investigations/2026-11-05-underinvested-book.md (Q2 table). On 11-04 the
earnings blackout (x0.5 on the target) and REDUCE_GROSS_20 (x0.8 on the target) turned NKE,
CLX, AKAM and CF into reductions and erased the COHR add.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import sqlalchemy as sa

import analysis.db  # noqa: F401
import factors.db  # noqa: F401
import portfolio.db  # noqa: F401
import risk.db  # noqa: F401
from data.db import daily_prices, earnings_calendar, initialise_schema
from factors.db import factor_scores
from portfolio.db import portfolio_positions, position_approvals
from risk.pre_trade import run_pre_trade
from risk.tail_risk import run_tail_risk

_SCORE_DATE = "2026-11-04"

# ticker, action, current, Layer 4 target, earnings date (None = not near earnings)
_ROWS = [
    ("KIM", "BUY", 0, 192, None),
    ("ARE", "SHORT", -42, -92, None),
    ("AVY", "BUY", 0, 24, None),
    ("CLX", "SHORT", -21, -44, "2026-11-02"),
    ("BA", "SHORT", -14, -28, None),
    ("CVNA", "SHORT", -6, -20, None),
    ("AIZ", "BUY", 4, 16, "2026-11-03"),
    ("NKE", "SHORT", -41, -50, None),
    ("COIN", "SHORT", -10, -17, None),
    ("ZTS", "COVER", -31, -25, None),
    ("AXON", "SHORT", 0, -4, "2026-11-05"),
    ("COHR", "SHORT", -2, -6, "2026-11-04"),
    ("AKAM", "SHORT", -10, -14, "2026-11-05"),
    ("CF", "BUY", 10, 13, "2026-11-04"),
    ("MU", "BUY", 3, 5, None),
    ("TXN", "SELL", 11, 10, None),
]


@pytest.fixture(autouse=True)
def _no_fred_key(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)


@pytest.fixture
def conn():
    engine = sa.create_engine("sqlite:///:memory:", future=True)
    initialise_schema(engine)
    with engine.connect() as c:
        yield c
    engine.dispose()


def _config():
    return {
        "portfolio": {"nav_usd": 10_000_000},
        "risk": {
            "pre_trade": {
                "adv_lookback": 20,
                "adv_pct": 1000.0,
                "max_position_pct": 0.05,
                "max_sector_pct": 1.0,
                "max_gross": 1.65,
                "net_min": -0.50,
                "net_max": 0.50,
                "max_net_beta": 5.0,
                "max_pairwise_corr": 1.01,
                "corr_lookback": 60,
                "earnings_blackout_days": 5,
            },
            "tail_risk": {
                "vix_caution": 25.0,
                "vix_stress": 35.0,
                "credit_spread_sigma": 2.0,
                "credit_lookback_days": 252,
            },
        },
    }


def _seed(conn):
    start = date.fromisoformat(_SCORE_DATE) - timedelta(days=40)
    prices = []
    for n, (ticker, action, cur, tgt, earnings) in enumerate(_ROWS):
        for i in range(41):
            p = 50.0 + n + (i % (n + 2)) * 0.3
            d = str(start + timedelta(days=i))
            prices.append(
                {
                    "ticker": ticker,
                    "date": d,
                    "open": p,
                    "high": p,
                    "low": p,
                    "close": p,
                    "adj_close": p,
                    "volume": 1_000_000,
                }
            )
        conn.execute(
            factor_scores.insert().values(ticker=ticker, score_date=_SCORE_DATE, sector=f"S{n}")
        )
        if cur:
            conn.execute(
                portfolio_positions.insert().values(
                    ticker=ticker,
                    direction="LONG" if cur > 0 else "SHORT",
                    shares=float(cur),
                    current_price=50.0,
                    market_value=cur * 50.0,
                    weight=cur * 50.0 / 1e7,
                    sector=f"S{n}",
                )
            )
        if earnings:
            conn.execute(earnings_calendar.insert().values(ticker=ticker, earnings_date=earnings))
        conn.execute(
            position_approvals.insert().values(
                rebalance_date=_SCORE_DATE,
                ticker=ticker,
                action=action,
                target_shares=float(tgt),
                current_shares=float(cur),
                delta_shares=float(tgt - cur),
                status="PENDING",
                created_at="2026-11-04T22:10:27",
            )
        )
    prices.append(
        {
            "ticker": "^VIX",
            "date": _SCORE_DATE,
            "open": 18.0,
            "high": 18.0,
            "low": 18.0,
            "close": 18.0,
            "adj_close": 18.0,
            "volume": 0,
        }
    )
    conn.execute(daily_prices.insert(), prices)
    conn.commit()


def test_planned_actions_survive_layer5(conn, tmp_path, monkeypatch):
    import risk.tail_risk as tr

    monkeypatch.setattr(tr, "_get_credit_spread_z", lambda *a, **k: 2.57)  # 11-04, 252 obs
    _seed(conn)

    run_pre_trade(conn, _SCORE_DATE, _config(), tmp_path, whatif=False)
    result = run_tail_risk(conn, _SCORE_DATE, _config(), tmp_path, whatif=False)
    assert result["tail_risk_state"] == "CAUTION"

    rows = conn.execute(
        sa.select(
            position_approvals.c.ticker,
            position_approvals.c.action,
            position_approvals.c.target_shares,
            position_approvals.c.delta_shares,
            position_approvals.c.status,
        )
    ).fetchall()
    got = {r.ticker: (r.action, r.target_shares, r.delta_shares, r.status) for r in rows}
    want = {t: (a, float(tgt), float(tgt - cur), "APPROVED") for t, a, cur, tgt, _ in _ROWS}
    assert got == want
