"""Reproductions for docs/investigations/2026-11-05-underinvested-book.md.

Each test states the correct behaviour and is marked xfail(strict=True): it fails today because
of the bug it names, and it will XPASS (and so fail the suite) once the bug is fixed. That is the
signal to delete the xfail marker and move the test next to the fix.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import pytest
import sqlalchemy as sa

import analysis.db  # noqa: F401
import execution.db  # noqa: F401
import factors.db  # noqa: F401
import portfolio.db  # noqa: F401
import reporting.db  # noqa: F401
import risk.db  # noqa: F401
from data.db import initialise_schema
from execution.order_sync import _order_values
from factors.db import factor_scores
from portfolio.db import portfolio_positions
from portfolio.rebalance import generate_trades
from reporting import tear_sheet
from reporting.db import portfolio_nav

_NAV = 100_000.0


def _engine(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 't.db'}", future=True)
    initialise_schema(engine)
    return engine


def _book_from_cash():
    """40-name 150% gross target (2026-11-04 shape: 20 long / 20 short at 3.75% each)."""
    rows = []
    for i in range(40):
        long = i < 20
        rows.append(
            {
                "ticker": f"T{i:02d}",
                "shares": (1 if long else -1) * 0.0375 * _NAV / 100.0,
                "direction": "LONG" if long else "SHORT",
                "combined_score": 95.0 - i * 0.1 if long else 5.0 + i * 0.1,
            }
        )
    target = pd.DataFrame(rows)
    prices = {t: pd.DataFrame({"close": [100.0]}) for t in target["ticker"]}
    return target, prices


@pytest.mark.xfail(
    strict=True,
    reason="Q1: turnover_budget_pct (30%) is applied per run to the whole build from cash",
)
def test_build_from_cash_not_capped_by_turnover_budget():
    target, prices = _book_from_cash()
    config = {"portfolio": {"nav_usd": _NAV, "turnover_budget_pct": 0.30}}
    trades = generate_trades(pd.DataFrame(), target, prices, config, None, "2026-11-04", False)
    opened = trades[trades["action"] != "HOLD"]
    # Expected: initial build-up from cash is not counted as turnover, so all 40 names trade.
    assert len(opened) == 40


@pytest.mark.xfail(strict=True, reason="Q3.3: tear sheet beta is hard-coded to 0.0")
def test_tear_sheet_beta_is_computed(tmp_path):
    engine = _engine(tmp_path)
    spy = [500.0, 505.0, 500.0, 510.0, 505.0, 515.0]
    with engine.begin() as conn:
        nav = _NAV
        for i, s in enumerate(spy):
            if i:
                nav *= 1 + 2 * (s / spy[i - 1] - 1)  # portfolio moves 2x SPY: beta 2
            conn.execute(
                portfolio_nav.insert().values(
                    date=f"2026-11-0{i + 1}", nav=nav, spy_close=s, drawdown_pct=0.0
                )
            )
    out = tmp_path / "tear.md"
    tear_sheet.write(engine, {}, output_path=str(out))
    beta_row = next(ln for ln in out.read_text().splitlines() if ln.startswith("| Beta |"))
    assert float(beta_row.split("|")[2]) == pytest.approx(2.0, abs=0.01)


@pytest.mark.xfail(
    strict=True, reason="Q3.3: tear sheet factor block is the newest factor_scores row"
)
def test_tear_sheet_factor_exposures_come_from_the_book(tmp_path):
    engine = _engine(tmp_path)
    with engine.begin() as conn:
        for d in ("2026-11-03", "2026-11-04"):
            conn.execute(portfolio_nav.insert().values(date=d, nav=_NAV, drawdown_pct=0.0))
        conn.execute(
            portfolio_positions.insert().values(
                ticker="HELD", direction="LONG", weight=0.05, sector="IT"
            )
        )
        conn.execute(
            factor_scores.insert(),
            [
                {"ticker": "HELD", "score_date": "2026-11-04", "momentum_score": 90.0},
                # Not in the book; the 2026-11-04 tear sheet printed ZTS's own row this way.
                {"ticker": "ZZZ", "score_date": "2026-11-05", "momentum_score": 23.3},
            ],
        )
    out = tmp_path / "tear.md"
    tear_sheet.write(engine, {}, output_path=str(out))
    assert "| Momentum | 23.3 |" not in out.read_text()


@pytest.mark.xfail(
    strict=True, reason="Q3.3: slippage is measured against the limit, so it is never adverse"
)
def test_buy_filled_above_decision_close_is_adverse_slippage():
    # Decision close 100.00; executor limit = close x 1.005 = 100.50; filled at the limit.
    order = SimpleNamespace(
        status="filled", filled_qty=10, filled_avg_price=100.50, limit_price=100.50
    )
    values = _order_values(order, "BUY")
    # Expected: about +50 bps adverse versus the 100.00 decision price. Today: 0.0.
    assert values["slippage_bps"] > 0
