"""Tests for portfolio/state.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import portfolio.db  # noqa: F401

import pandas as pd
import pytest
import sqlalchemy as sa

from portfolio.db import portfolio_positions, portfolio_history
from portfolio.state import get_nav, load_positions, save_positions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_positions(tickers):
    """Create a minimal positions DataFrame for save_positions."""
    rows = []
    for i, ticker in enumerate(tickers):
        rows.append({
            "ticker":        ticker,
            "direction":     "LONG",
            "shares":        float(100 + i * 10),
            "entry_price":   50.0,
            "entry_date":    "2026-01-01",
            "current_price": 55.0,
            "sector":        "Technology",
            "combined_score": 75.0,
            "beta":          1.1,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# load_positions
# ---------------------------------------------------------------------------

class TestLoadPositions:
    def test_load_empty_returns_empty_df(self, tmp_db):
        df = load_positions(tmp_db)
        assert df.empty


# ---------------------------------------------------------------------------
# save_positions / round-trip
# ---------------------------------------------------------------------------

class TestSavePositions:
    def test_save_and_load_roundtrip(self, tmp_db):
        positions = _make_positions(["AAPL", "MSFT"])
        save_positions(tmp_db, positions, "2026-03-01", nav_usd=1_000_000)
        loaded = load_positions(tmp_db)
        assert set(loaded["ticker"]) == {"AAPL", "MSFT"}

    def test_save_appends_to_history(self, tmp_db):
        positions = _make_positions(["AAPL", "MSFT"])
        save_positions(tmp_db, positions, "2026-03-01", nav_usd=1_000_000)
        save_positions(tmp_db, positions, "2026-03-08", nav_usd=1_000_000)
        rows = tmp_db.execute(
            sa.select(portfolio_history.c.snapshot_date, portfolio_history.c.ticker)
            .where(portfolio_history.c.ticker == "AAPL")
        ).fetchall()
        # AAPL should appear twice (once per snapshot)
        assert len(rows) == 2

    def test_upsert_overwrites_existing(self, tmp_db):
        positions = _make_positions(["AAPL"])
        save_positions(tmp_db, positions, "2026-03-01", nav_usd=1_000_000)
        # Update AAPL's current_price and save again
        positions2 = positions.copy()
        positions2["current_price"] = 60.0
        save_positions(tmp_db, positions2, "2026-03-01", nav_usd=1_000_000)
        rows = tmp_db.execute(
            sa.select(portfolio_positions).where(portfolio_positions.c.ticker == "AAPL")
        ).fetchall()
        # Only one row should exist in portfolio_positions (upsert)
        assert len(rows) == 1
        # The current_price should reflect the latest save
        assert rows[0]._mapping["current_price"] == pytest.approx(60.0)


# ---------------------------------------------------------------------------
# get_nav
# ---------------------------------------------------------------------------

class TestGetNav:
    def test_get_nav_from_config(self):
        config = {"portfolio": {"nav_usd": 5_000_000}}
        assert get_nav(config) == pytest.approx(5_000_000.0)

    def test_get_nav_default(self):
        assert get_nav({}) == pytest.approx(10_000_000.0)
