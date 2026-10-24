"""Tests for portfolio/state.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import pytest

import portfolio.db  # noqa: F401
import reporting.db  # noqa: F401
from portfolio.db import portfolio_positions
from portfolio.state import get_nav, load_positions, with_nav
from reporting.db import portfolio_nav

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


class TestRecordedNav:
    """Packet 50: NAV from portfolio_nav (Alpaca equity), config as fallback."""

    def _record(self, conn, navs):
        conn.execute(portfolio_nav.insert(), [{"date": d, "nav": n} for d, n in navs.items()])
        conn.commit()

    def test_latest_recorded_nav(self, tmp_db):
        self._record(tmp_db, {"2026-10-22": 100_000.0, "2026-10-23": 101_000.0})
        assert get_nav({"portfolio": {"nav_usd": 5.0}}, tmp_db) == pytest.approx(101_000.0)

    def test_on_or_before_score_date(self, tmp_db):
        self._record(tmp_db, {"2026-10-22": 100_000.0, "2026-10-26": 120_000.0})
        nav = get_nav({}, tmp_db, "2026-10-23")
        assert nav == pytest.approx(100_000.0)

    def test_falls_back_to_config(self, tmp_db):
        assert get_nav({"portfolio": {"nav_usd": 5_000_000}}, tmp_db) == pytest.approx(5_000_000)

    def test_non_positive_nav_ignored(self, tmp_db):
        # Adversarial: a zero equity reading must not size the book to nothing.
        self._record(tmp_db, {"2026-10-22": 100_000.0, "2026-10-23": 0.0})
        assert get_nav({}, tmp_db, "2026-10-23") == pytest.approx(100_000.0)

    def test_with_nav_copies_config(self):
        config = {"portfolio": {"nav_usd": 1.0, "num_longs": 20}, "risk": {"x": 1}}
        out = with_nav(config, 2.0)
        assert out["portfolio"] == {"nav_usd": 2.0, "num_longs": 20}
        assert out["risk"] == {"x": 1}
        assert config["portfolio"]["nav_usd"] == 1.0  # original untouched
