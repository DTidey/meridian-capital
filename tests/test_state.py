"""Tests for portfolio/state.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import pytest

import portfolio.db  # noqa: F401
from portfolio.db import portfolio_positions
from portfolio.state import get_nav, load_positions

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert(conn, ticker, shares, direction):
    conn.execute(
        portfolio_positions.insert().values(ticker=ticker, direction=direction, shares=shares)
    )
    conn.commit()


# ---------------------------------------------------------------------------
# load_positions
# ---------------------------------------------------------------------------


class TestLoadPositions:
    def test_load_empty_returns_empty_df(self, tmp_db):
        df = load_positions(tmp_db)
        assert df.empty

    def test_load_returns_signed_shares(self, tmp_db):
        _insert(tmp_db, "AAPL", 10.0, "LONG")
        _insert(tmp_db, "BRK-B", -5.0, "SHORT")
        df = load_positions(tmp_db).set_index("ticker")
        assert df.loc["BRK-B", "shares"] == pytest.approx(-5.0)
        assert df.loc["AAPL", "shares"] == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# get_nav
# ---------------------------------------------------------------------------


class TestGetNav:
    def test_get_nav_from_config(self):
        config = {"portfolio": {"nav_usd": 5_000_000}}
        assert get_nav(config) == pytest.approx(5_000_000.0)

    def test_get_nav_default(self):
        assert get_nav({}) == pytest.approx(10_000_000.0)
