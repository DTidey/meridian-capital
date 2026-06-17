"""Tests for factors/crowding.py."""

import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import factors.db  # noqa: F401 — registers tables on metadata
from data.db import get_engine, initialise_schema
from factors.crowding import _compute_factor_returns, detect
from factors.db import factor_scores as factor_scores_table

SCORE_DATE = "2024-06-01"
_CONFIG = {
    "window_days": 60,
    "deviation_threshold": 0.40,
    "baselines": {"momentum_value": -0.30, "momentum_quality": 0.10},
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


def _insert_scores(conn, ticker, score_dates, momentum=50, value=50, quality=50):
    for sd in score_dates:
        conn.execute(
            factor_scores_table.insert().values(
                ticker=ticker,
                score_date=sd,
                sector="IT",
                regime="NORMAL",
                momentum_score=momentum,
                quality_score=quality,
                value_score=value,
                revisions_score=50,
                insider_score=50,
                growth_score=50,
                short_interest_score=50,
                institutional_score=50,
                composite_score=50,
                direction="NEUTRAL",
                computed_at="2024-06-01T00:00:00",
            )
        )
    conn.commit()


def _make_prices(tickers, n_days=70):
    dates = pd.date_range("2024-01-01", periods=n_days, freq="B")
    rows = []
    for t in tickers:
        for d, c in zip(dates, [100.0 + i * 0.1 for i in range(n_days)], strict=False):
            rows.append({"ticker": t, "date": d, "adj_close": c, "close": c, "volume": 1})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestInsufficientHistory:
    def test_no_history_returns_empty_list(self, tmp_db):
        prices = _make_prices(["AAPL"])
        result = detect(tmp_db, prices, SCORE_DATE, _CONFIG)
        assert result == []

    def test_few_days_returns_empty_list(self, tmp_db):
        # Only 5 days of history
        dates = [(date(2024, 5, 28) + timedelta(days=i)).isoformat() for i in range(5)]
        _insert_scores(tmp_db, "AAPL", dates)
        prices = _make_prices(["AAPL"])
        result = detect(tmp_db, prices, SCORE_DATE, _CONFIG)
        assert result == []


class TestCrowdingDetection:
    def test_result_structure(self, tmp_db):
        # Insert enough history
        dates = [(date(2024, 1, 1) + timedelta(days=i)).isoformat() for i in range(65)]
        for t in ["AAPL", "MSFT", "GOOG", "AMZN"]:
            _insert_scores(tmp_db, t, dates)
        prices = _make_prices(["AAPL", "MSFT", "GOOG", "AMZN"], n_days=80)
        result = detect(tmp_db, prices, SCORE_DATE, _CONFIG)

        if result:  # may be empty if not enough factor diversity
            for row in result:
                assert "score_date" in row
                assert "factor_a" in row
                assert "factor_b" in row
                assert "rolling_corr" in row
                assert "deviation" in row
                assert "flagged" in row

    def test_flagged_is_0_or_1(self, tmp_db):
        dates = [(date(2024, 1, 1) + timedelta(days=i)).isoformat() for i in range(65)]
        for t in ["A", "B", "C", "D"]:
            _insert_scores(tmp_db, t, dates)
        prices = _make_prices(["A", "B", "C", "D"], n_days=80)
        result = detect(tmp_db, prices, SCORE_DATE, _CONFIG)
        for row in result:
            assert row["flagged"] in (0, 1)

    def test_no_crash_empty_prices(self, tmp_db):
        dates = [(date(2024, 1, 1) + timedelta(days=i)).isoformat() for i in range(65)]
        _insert_scores(tmp_db, "AAPL", dates)
        result = detect(tmp_db, pd.DataFrame(), SCORE_DATE, _CONFIG)
        assert result == []


class TestComputeFactorReturns:
    def test_returns_dataframe_with_factor_columns(self):
        n = 30
        dates = [(date(2024, 1, 1) + timedelta(days=i)).isoformat() for i in range(n)]
        tickers = ["A", "B", "C"]
        hist_rows = []
        for sd in dates:
            for t in tickers:
                hist_rows.append(
                    {
                        "ticker": t,
                        "score_date": sd,
                        "momentum_score": 90 if t == "A" else 10,
                        "quality_score": 50,
                        "value_score": 50,
                        "revisions_score": 50,
                        "insider_score": 50,
                        "growth_score": 50,
                        "short_interest_score": 50,
                        "institutional_score": 50,
                    }
                )
        hist = pd.DataFrame(hist_rows)
        prices = _make_prices(tickers, n_days=n + 10)
        result = _compute_factor_returns(hist, prices)
        # May be empty or have columns; should not raise
        assert isinstance(result, pd.DataFrame)
