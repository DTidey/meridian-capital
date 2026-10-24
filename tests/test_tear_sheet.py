"""Packet 44: tear sheet sector exposures with signed position weights."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlalchemy as sa

import analysis.db  # noqa: F401
import execution.db  # noqa: F401
import factors.db  # noqa: F401
import portfolio.db  # noqa: F401
import reporting.db  # noqa: F401
import risk.db  # noqa: F401
from data.db import initialise_schema
from portfolio.db import portfolio_positions
from reporting import tear_sheet
from reporting.db import portfolio_nav


def test_sector_net_weight_signed(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 't.db'}", future=True)
    initialise_schema(engine)
    with engine.begin() as conn:
        for d, nav in [("2026-10-22", 100_000.0), ("2026-10-23", 101_000.0)]:
            conn.execute(portfolio_nav.insert().values(date=d, nav=nav, drawdown_pct=0.0))
        conn.execute(
            portfolio_positions.insert(),
            [
                {"ticker": "A", "direction": "LONG", "weight": 0.05, "sector": "IT"},
                {"ticker": "B", "direction": "SHORT", "weight": -0.03, "sector": "IT"},
            ],
        )
    out = tmp_path / "tear.md"
    tear_sheet.write(engine, {}, output_path=str(out))
    assert "| IT | 5.00% | -3.00% | +2.00% |" in out.read_text()
