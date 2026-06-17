"""Market data ingestion — unit tests for helpers and DB round-trips."""

import math
from datetime import datetime, timedelta

import pandas as pd
import pytest
import sqlalchemy as sa

from data.db import daily_prices
from data.market_data import _float, _last_stored_date, _upsert_prices


# ---------------------------------------------------------------------------
# _float
# ---------------------------------------------------------------------------

class TestFloat:
    def test_valid_number(self):
        assert _float(123.45) == pytest.approx(123.45)

    def test_integer(self):
        assert _float(100) == pytest.approx(100.0)

    def test_string_number(self):
        assert _float("99.9") == pytest.approx(99.9)

    def test_none_returns_none(self):
        assert _float(None) is None

    def test_nan_returns_none(self):
        assert _float(float("nan")) is None

    def test_pandas_nan_returns_none(self):
        assert _float(pd.NA) is None

    def test_non_numeric_string_returns_none(self):
        assert _float("N/A") is None

    def test_zero_is_valid(self):
        assert _float(0) == pytest.approx(0.0)

    def test_negative(self):
        assert _float(-42.5) == pytest.approx(-42.5)


# ---------------------------------------------------------------------------
# _last_stored_date
# ---------------------------------------------------------------------------

class TestLastStoredDate:
    def test_empty_table_returns_none(self, tmp_db):
        assert _last_stored_date(tmp_db, "AAPL") is None

    def test_returns_most_recent_date(self, tmp_db):
        for date in ("2024-01-01", "2024-03-15", "2024-02-10"):
            tmp_db.execute(sa.insert(daily_prices).values(ticker="AAPL", date=date, close=100))
        tmp_db.commit()
        assert _last_stored_date(tmp_db, "AAPL") == "2024-03-15"

    def test_isolated_per_ticker(self, tmp_db):
        tmp_db.execute(sa.insert(daily_prices).values(ticker="AAPL", date="2024-06-01", close=100))
        tmp_db.execute(sa.insert(daily_prices).values(ticker="MSFT", date="2024-01-01", close=200))
        tmp_db.commit()
        assert _last_stored_date(tmp_db, "AAPL") == "2024-06-01"
        assert _last_stored_date(tmp_db, "MSFT") == "2024-01-01"
        assert _last_stored_date(tmp_db, "GOOGL") is None


# ---------------------------------------------------------------------------
# _upsert_prices
# ---------------------------------------------------------------------------

def _make_price_df(dates, closes, volumes=None):
    """Build a minimal OHLCV DataFrame matching yfinance output."""
    idx = pd.to_datetime(dates)
    n = len(dates)
    return pd.DataFrame(
        {
            "Open":      closes,
            "High":      closes,
            "Low":       closes,
            "Close":     closes,
            "Adj Close": closes,
            "Volume":    volumes if volumes else [1_000_000] * n,
        },
        index=idx,
    )


class TestUpsertPrices:
    def test_inserts_rows(self, tmp_db):
        df = _make_price_df(["2024-01-02", "2024-01-03"], [150.0, 151.0])
        count = _upsert_prices(tmp_db, df, "AAPL")
        assert count == 2
        rows = tmp_db.execute(
            sa.select(daily_prices.c.date, daily_prices.c.close).order_by(daily_prices.c.date)
        ).fetchall()
        assert rows == [("2024-01-02", 150.0), ("2024-01-03", 151.0)]

    def test_empty_dataframe_returns_zero(self, tmp_db):
        assert _upsert_prices(tmp_db, pd.DataFrame(), "AAPL") == 0

    def test_nan_volume_stored_as_zero(self, tmp_db):
        df = _make_price_df(["2024-01-02"], [100.0], volumes=[float("nan")])
        _upsert_prices(tmp_db, df, "AAPL")
        row = tmp_db.execute(sa.select(daily_prices.c.volume)).fetchone()
        assert row[0] == 0

    def test_upsert_replaces_existing_row(self, tmp_db):
        df1 = _make_price_df(["2024-01-02"], [100.0])
        df2 = _make_price_df(["2024-01-02"], [999.0])
        _upsert_prices(tmp_db, df1, "AAPL")
        _upsert_prices(tmp_db, df2, "AAPL")
        row = tmp_db.execute(
            sa.select(daily_prices.c.close).where(daily_prices.c.date == "2024-01-02")
        ).fetchone()
        assert row[0] == 999.0

    def test_nan_ohlcv_stored_as_null(self, tmp_db):
        df = _make_price_df(["2024-01-02"], [float("nan")])
        _upsert_prices(tmp_db, df, "AAPL")
        row = tmp_db.execute(sa.select(daily_prices.c.close)).fetchone()
        assert row[0] is None

    def test_returns_correct_count(self, tmp_db):
        df = _make_price_df(
            ["2024-01-02", "2024-01-03", "2024-01-04"],
            [100.0, 101.0, 102.0],
        )
        assert _upsert_prices(tmp_db, df, "MSFT") == 3
