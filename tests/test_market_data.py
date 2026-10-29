"""Market data ingestion — unit tests for helpers and DB round-trips."""

import logging
import warnings
from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
import sqlalchemy as sa
from pandas.errors import Pandas4Warning

from data.db import daily_prices
from data.market_data import (
    _download_end,
    _float,
    _last_stored_date,
    _upsert_prices,
    ignore_yfinance_inplace_warnings,
    update_prices,
)
from data.providers import PriceProvider

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
            "Open": closes,
            "High": closes,
            "Low": closes,
            "Close": closes,
            "Adj Close": closes,
            "Volume": volumes if volumes else [1_000_000] * n,
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

    def test_all_null_row_not_stored(self, tmp_db):
        df = _make_price_df(["2024-01-02", "2024-01-03"], [float("nan"), 101.0])
        assert _upsert_prices(tmp_db, df, "AAPL") == 1
        rows = tmp_db.execute(sa.select(daily_prices.c.date)).fetchall()
        assert rows == [("2024-01-03",)]

    def test_row_with_only_close_is_stored(self, tmp_db):
        df = _make_price_df(["2024-01-02"], [100.0])
        df[["Open", "High", "Low"]] = float("nan")
        assert _upsert_prices(tmp_db, df, "AAPL") == 1
        row = tmp_db.execute(sa.select(daily_prices.c.open, daily_prices.c.close)).fetchone()
        assert row == (None, 100.0)

    def test_volume_without_prices_not_stored(self, tmp_db):
        df = _make_price_df(["2024-01-02"], [float("nan")], volumes=[5_000])
        assert _upsert_prices(tmp_db, df, "AAPL") == 0
        assert tmp_db.execute(sa.select(sa.func.count()).select_from(daily_prices)).scalar() == 0

    def test_volume_above_int32(self, tmp_db):
        assert isinstance(daily_prices.c.volume.type, sa.BigInteger)
        df = _make_price_df(["2024-01-02"], [100.0], volumes=[5_000_000_000])
        _upsert_prices(tmp_db, df, "AAPL")
        assert tmp_db.execute(sa.select(daily_prices.c.volume)).scalar() == 5_000_000_000

    def test_returns_correct_count(self, tmp_db):
        df = _make_price_df(
            ["2024-01-02", "2024-01-03", "2024-01-04"],
            [100.0, 101.0, 102.0],
        )
        assert _upsert_prices(tmp_db, df, "MSFT") == 3


# ---------------------------------------------------------------------------
# Download end date
# ---------------------------------------------------------------------------

_NY = ZoneInfo("America/New_York")


class TestDownloadEnd:
    def test_before_close_is_today(self):
        assert _download_end(datetime(2026, 10, 21, 16, 29, tzinfo=_NY)) == "2026-10-21"

    def test_at_close_threshold_is_tomorrow(self):
        assert _download_end(datetime(2026, 10, 21, 16, 30, tzinfo=_NY)) == "2026-10-22"

    def test_after_close_is_tomorrow(self):
        assert _download_end(datetime(2026, 10, 21, 18, 0, tzinfo=_NY)) == "2026-10-22"

    def test_dst_boundary(self):
        # 21:15 UTC is 17:15 EDT on Friday 2026-10-30 but 16:15 EST on Monday 2026-11-02
        assert _download_end(datetime(2026, 10, 30, 21, 15, tzinfo=UTC)) == "2026-10-31"
        assert _download_end(datetime(2026, 11, 2, 21, 15, tzinfo=UTC)) == "2026-11-02"

    def test_cron_time_includes_day_in_summer_and_winter(self):
        assert _download_end(datetime(2026, 7, 15, 22, 0, tzinfo=UTC)) == "2026-07-16"
        assert _download_end(datetime(2026, 12, 15, 22, 0, tzinfo=UTC)) == "2026-12-16"

    def test_new_york_date_used_after_utc_midnight(self):
        # 01:00 UTC on 10-22 is 21:00 New York on 10-21: that session has closed
        assert _download_end(datetime(2026, 10, 22, 1, 0, tzinfo=UTC)) == "2026-10-22"

    def test_update_prices_stores_todays_bar_after_close(self, tmp_db, yahoo):
        _seed(tmp_db, "AAA", _DAYS[:-1])
        yahoo.history["AAA"] = _history(_DAYS)
        _run(tmp_db, ["AAA"])
        assert yahoo.calls == [(("AAA",), "2026-10-20", "2026-10-22")]
        assert _stored(tmp_db, "AAA")[-1][0] == "2026-10-21"

    def test_update_prices_skips_partial_bar_before_close(self, tmp_db, yahoo, monkeypatch):
        monkeypatch.setattr(
            "data.market_data._ny_now", lambda: datetime(2026, 10, 21, 12, 0, tzinfo=_NY)
        )
        _seed(tmp_db, "AAA", _DAYS[:-1])
        yahoo.history["AAA"] = _history(_DAYS)
        _run(tmp_db, ["AAA"])
        assert yahoo.calls == [(("AAA",), "2026-10-20", "2026-10-21")]
        assert _stored(tmp_db, "AAA")[-1][0] == "2026-10-20"


# ---------------------------------------------------------------------------
# update_prices: adjustment check, gap check, full re-download
# ---------------------------------------------------------------------------

_DAYS = ["2026-10-14", "2026-10-15", "2026-10-16", "2026-10-19", "2026-10-20", "2026-10-21"]
_CUTOFF = "2023-10-22"  # three years before the patched UTC now
_CONFIG = {"market_data": {"lookback_years": 3}}


class _FakeYahoo:
    """yfinance batch download over a fixed history; `end` is exclusive, as in yfinance."""

    def __init__(self):
        self.history: dict[str, pd.DataFrame] = {}
        self.calls: list[tuple] = []
        self.empty_full_fetch = False

    def batch(self, tickers, start, end):
        self.calls.append((tuple(tickers), start, end))
        if self.empty_full_fetch and start == _CUTOFF:
            return {}
        out = {}
        for ticker in tickers:
            df = self.history.get(ticker)
            if df is not None:
                sub = df[(df.index >= start) & (df.index < end)]
                if not sub.empty:
                    out[ticker] = sub
        return out


@pytest.fixture
def yahoo(monkeypatch):
    fake = _FakeYahoo()
    monkeypatch.setattr("data.market_data._fetch_yfinance_batch", fake.batch)
    monkeypatch.setattr("data.market_data._fetch_yfinance_single", lambda *a: pd.DataFrame())
    monkeypatch.setattr(
        "data.market_data._ny_now", lambda: datetime(2026, 10, 21, 18, 0, tzinfo=_NY)
    )
    monkeypatch.setattr("data.market_data.utc_now", lambda: datetime(2026, 10, 21, 22, 0))
    return fake


def _history(dates, close=100.0, adj=None):
    df = _make_price_df(dates, [close] * len(dates))
    df["Adj Close"] = adj if adj is not None else close
    return df


def _seed(conn, ticker, dates, close=100.0, adj=None):
    rows = [
        {
            "ticker": ticker,
            "date": d,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "adj_close": adj if adj is not None else close,
            "volume": 1,
        }
        for d in dates
    ]
    conn.execute(sa.insert(daily_prices), rows)
    conn.commit()


def _stored(conn, ticker):
    return conn.execute(
        sa.select(daily_prices.c.date, daily_prices.c.close, daily_prices.c.adj_close)
        .where(daily_prices.c.ticker == ticker)
        .order_by(daily_prices.c.date)
    ).fetchall()


def _run(conn, tickers, refresh=False):
    providers = SimpleNamespace(prices=PriceProvider.YFINANCE, polygon_key="")
    return update_prices(conn, tickers, _CONFIG, providers, refresh=refresh)


def _full_fetches(fake):
    return {t for tickers, start, _ in fake.calls if start == _CUTOFF for t in tickers}


class TestAdjustmentCheck:
    def test_split_triggers_full_redownload(self, tmp_db, yahoo):
        _seed(tmp_db, "APH", _DAYS[:-1], close=200.0)
        yahoo.history["APH"] = _history(_DAYS, close=100.0)  # 2:1 split, back-adjusted
        _run(tmp_db, ["APH"])
        assert _full_fetches(yahoo) == {"APH"}
        assert [r[1] for r in _stored(tmp_db, "APH")] == [100.0] * 6

    def test_small_dividend_triggers_full_redownload(self, tmp_db, yahoo):
        _seed(tmp_db, "AAPL", _DAYS[:-1], close=100.0, adj=100.0)
        yahoo.history["AAPL"] = _history(_DAYS, close=100.0, adj=99.92)  # 0.08%
        _run(tmp_db, ["AAPL"])
        assert _full_fetches(yahoo) == {"AAPL"}
        assert {r[2] for r in _stored(tmp_db, "AAPL")} == {99.92}

    def test_unchanged_history_no_redownload(self, tmp_db, yahoo):
        _seed(tmp_db, "MSFT", _DAYS[:-1])
        yahoo.history["MSFT"] = _history(_DAYS)
        summary = _run(tmp_db, ["MSFT"])
        assert _full_fetches(yahoo) == set()
        assert summary == {"MSFT": 2}  # overlap row re-stored plus today

    def test_difference_within_tolerance_no_redownload(self, tmp_db, yahoo):
        _seed(tmp_db, "MSFT", _DAYS[:-1], close=10000.0, adj=10000.0)
        yahoo.history["MSFT"] = _history(_DAYS, close=10000.0, adj=10001.0)  # exactly 0.01%
        _run(tmp_db, ["MSFT"])
        assert _full_fetches(yahoo) == set()
        assert _stored(tmp_db, "MSFT")[0][2] == 10000.0  # older rows untouched

    def test_stale_row_removed(self, tmp_db, yahoo):
        _seed(tmp_db, "KLAC", _DAYS[:-1], close=200.0)
        _seed(tmp_db, "KLAC", ["2026-10-01"], close=200.0)  # provider no longer has this date
        yahoo.history["KLAC"] = _history(_DAYS, close=100.0)
        _run(tmp_db, ["KLAC"])
        assert [r[0] for r in _stored(tmp_db, "KLAC")] == _DAYS

    def test_no_provider_row_for_last_date_no_redownload(self, tmp_db, yahoo):
        _seed(tmp_db, "CVNA", _DAYS[:-1], close=200.0)
        yahoo.history["CVNA"] = _history(_DAYS[-1:], close=100.0)
        _run(tmp_db, ["CVNA"])
        assert _full_fetches(yahoo) == set()

    def test_null_stored_close_compares_adj_close_only(self, tmp_db, yahoo):
        _seed(tmp_db, "HON", _DAYS[:-2])
        tmp_db.execute(
            sa.insert(daily_prices).values(ticker="HON", date=_DAYS[-2], adj_close=100.0)
        )
        tmp_db.commit()
        yahoo.history["HON"] = _history(_DAYS, close=50.0, adj=100.0)
        _run(tmp_db, ["HON"])
        assert _full_fetches(yahoo) == set()

    def test_redownload_reason_logged(self, tmp_db, yahoo, caplog):
        _seed(tmp_db, "APH", _DAYS[:-1], close=200.0)
        yahoo.history["APH"] = _history(_DAYS, close=100.0)
        with caplog.at_level(logging.INFO, logger="data.market_data"):
            _run(tmp_db, ["APH"])
        assert "APH: full re-download (adjustment)" in caplog.text
        assert "Prices: 1 full re-downloads (1 adjustment), 0 empty rows dropped" in caplog.text


class TestGapCheck:
    def test_missing_calendar_date_triggers_redownload(self, tmp_db, yahoo):
        for t in ("AAA", "BBB", "CCC"):
            _seed(tmp_db, t, _DAYS[:-1])
            yahoo.history[t] = _history(_DAYS)
        _seed(tmp_db, "DDD", [d for d in _DAYS[:-1] if d != "2026-10-15"])
        yahoo.history["DDD"] = _history(_DAYS)
        _run(tmp_db, ["AAA", "BBB", "CCC", "DDD"])
        assert _full_fetches(yahoo) == {"DDD"}
        assert [r[0] for r in _stored(tmp_db, "DDD")] == _DAYS

    def test_complete_history_no_redownload(self, tmp_db, yahoo):
        for t in ("AAA", "BBB"):
            _seed(tmp_db, t, _DAYS[:-1])
            yahoo.history[t] = _history(_DAYS)
        _run(tmp_db, ["AAA", "BBB"])
        assert _full_fetches(yahoo) == set()

    def test_gap_in_spy_detected(self, tmp_db, yahoo):
        for t in ("AAA", "BBB"):
            _seed(tmp_db, t, _DAYS[:-1])
            yahoo.history[t] = _history(_DAYS)
        _seed(tmp_db, "SPY", [d for d in _DAYS[:-1] if d != "2026-10-16"])
        yahoo.history["SPY"] = _history(_DAYS)
        _run(tmp_db, ["AAA", "BBB", "SPY"])
        assert _full_fetches(yahoo) == {"SPY"}

    def test_minority_date_is_not_a_calendar_date(self, tmp_db, yahoo):
        for t in ("AAA", "BBB", "CCC"):
            _seed(tmp_db, t, _DAYS[:-1])
            yahoo.history[t] = _history(_DAYS)
        _seed(tmp_db, "AAA", ["2026-10-17"])  # one ticker's stray Saturday row
        _run(tmp_db, ["AAA", "BBB", "CCC"])
        assert _full_fetches(yahoo) == set()

    def test_date_held_by_exactly_half_is_a_calendar_date(self, tmp_db, yahoo):
        for t in ("AAA", "BBB", "CCC", "DDD"):
            _seed(tmp_db, t, _DAYS[:-1])
            yahoo.history[t] = _history(_DAYS)
        _seed(tmp_db, "AAA", ["2026-10-17"])
        _seed(tmp_db, "BBB", ["2026-10-17"])
        _run(tmp_db, ["AAA", "BBB", "CCC", "DDD"])
        assert _full_fetches(yahoo) == {"CCC", "DDD"}

    def test_null_close_rows_do_not_make_a_calendar_date(self, tmp_db, yahoo):
        for t in ("AAA", "BBB"):
            _seed(tmp_db, t, _DAYS[:-1])
            yahoo.history[t] = _history(_DAYS)
            tmp_db.execute(sa.insert(daily_prices).values(ticker=t, date="2026-10-17"))
        tmp_db.commit()
        _seed(tmp_db, "CCC", _DAYS[:-1])
        yahoo.history["CCC"] = _history(_DAYS)
        _run(tmp_db, ["AAA", "BBB", "CCC"])
        assert _full_fetches(yahoo) == set()

    def test_null_row_on_calendar_date_is_a_gap(self, tmp_db, yahoo):
        for t in ("AAA", "BBB", "CCC"):
            _seed(tmp_db, t, _DAYS[:-1])
            yahoo.history[t] = _history(_DAYS)
        _seed(tmp_db, "DDD", [d for d in _DAYS[:-1] if d != "2026-10-15"])
        tmp_db.execute(sa.insert(daily_prices).values(ticker="DDD", date="2026-10-15"))
        tmp_db.commit()
        yahoo.history["DDD"] = _history(_DAYS)
        _run(tmp_db, ["AAA", "BBB", "CCC", "DDD"])
        assert _full_fetches(yahoo) == {"DDD"}

    def test_history_starting_late_is_not_a_gap(self, tmp_db, yahoo):
        for t in ("AAA", "BBB"):
            _seed(tmp_db, t, _DAYS[:-1])
            yahoo.history[t] = _history(_DAYS)
        _seed(tmp_db, "NEW", _DAYS[3:-1])  # listed recently
        yahoo.history["NEW"] = _history(_DAYS[3:])
        _run(tmp_db, ["AAA", "BBB", "NEW"])
        assert _full_fetches(yahoo) == set()


class TestFullRedownload:
    def test_empty_fetch_keeps_rows(self, tmp_db, yahoo, caplog):
        for t in ("AAA", "BBB"):
            _seed(tmp_db, t, _DAYS[:-1])
            yahoo.history[t] = _history(_DAYS)
        _seed(tmp_db, "DDD", [d for d in _DAYS[:-1] if d != "2026-10-15"], close=7.0)
        before = _stored(tmp_db, "DDD")
        with caplog.at_level(logging.WARNING, logger="data.market_data"):
            summary = _run(tmp_db, ["AAA", "BBB", "DDD"])
        assert _stored(tmp_db, "DDD") == before
        assert summary["DDD"] == 0
        assert "DDD" in caplog.text

    def test_empty_fetch_after_adjustment_keeps_rows(self, tmp_db, yahoo):
        _seed(tmp_db, "APH", _DAYS[:-1], close=200.0)
        before = _stored(tmp_db, "APH")
        yahoo.history["APH"] = _history(_DAYS, close=100.0)
        yahoo.empty_full_fetch = True
        _run(tmp_db, ["APH"])
        assert _stored(tmp_db, "APH") == before  # incremental bars not mixed in either

    def test_all_null_fetch_keeps_rows(self, tmp_db, yahoo):
        _seed(tmp_db, "APH", _DAYS[:-1], close=200.0)
        before = _stored(tmp_db, "APH")
        yahoo.history["APH"] = _history(_DAYS, close=float("nan"), adj=float("nan"))
        _run(tmp_db, ["APH"], refresh=True)
        assert _stored(tmp_db, "APH") == before

    def test_insert_failure_rolls_back_delete(self, tmp_db, yahoo, monkeypatch):
        _seed(tmp_db, "APH", _DAYS[:-1], close=200.0)
        before = _stored(tmp_db, "APH")
        dup = _history(["2026-10-20", "2026-10-20"], close=100.0)  # duplicate key on insert
        yahoo.history["APH"] = dup
        monkeypatch.setattr("data.market_data.insert_or_replace", lambda conn, t: sa.insert(t))
        with pytest.raises(sa.exc.IntegrityError):
            _run(tmp_db, ["APH"], refresh=True)
        assert _stored(tmp_db, "APH") == before

    def test_refresh_redownloads_all(self, tmp_db, yahoo, caplog):
        for t in ("AAA", "BBB"):
            _seed(tmp_db, t, ["2026-10-01", *_DAYS[:-1]], close=200.0)
            yahoo.history[t] = _history(_DAYS, close=200.0)
        with caplog.at_level(logging.INFO, logger="data.market_data"):
            summary = _run(tmp_db, ["AAA", "BBB"], refresh=True)
        assert yahoo.calls == [(("AAA", "BBB"), _CUTOFF, "2026-10-22")]
        assert summary == {"AAA": 6, "BBB": 6}
        for t in ("AAA", "BBB"):
            assert [r[0] for r in _stored(tmp_db, t)] == _DAYS
        assert "Prices: 2 full re-downloads (2 refresh), 0 empty rows dropped" in caplog.text

    def test_new_ticker_downloads_from_cutoff(self, tmp_db, yahoo, caplog):
        yahoo.history["NEW"] = _history(_DAYS)
        with caplog.at_level(logging.INFO, logger="data.market_data"):
            summary = _run(tmp_db, ["NEW"])
        assert yahoo.calls == [(("NEW",), _CUTOFF, "2026-10-22")]
        assert summary == {"NEW": 6}
        assert "Prices: 0 full re-downloads, 0 empty rows dropped" in caplog.text

    def test_empty_rows_dropped_counted(self, tmp_db, yahoo, caplog):
        df = _history(_DAYS)
        df.loc[pd.Timestamp("2026-10-16"), ["Open", "High", "Low", "Close", "Adj Close"]] = None
        yahoo.history["NEW"] = df
        with caplog.at_level(logging.INFO, logger="data.market_data"):
            summary = _run(tmp_db, ["NEW"])
        assert summary == {"NEW": 5}
        assert "1 empty rows dropped" in caplog.text


_INPLACE = "The inplace keyword in DataFrame.sort_index is deprecated"


def _shown(message, module):
    """Warnings recorded for one warning raised from module, under pytest's 'always' filters."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        ignore_yfinance_inplace_warnings()
        warnings.warn_explicit(message, Pandas4Warning, "x.py", 1, module=module, registry={})
    return caught


class TestYfinanceInplaceWarnings:
    def test_yfinance_inplace_hidden(self):
        assert _shown(_INPLACE, "yfinance.utils") == []
        assert _shown(_INPLACE, "yfinance.multi") == []

    def test_own_code_inplace_shown(self):
        assert len(_shown(_INPLACE, "data.market_data")) == 1

    def test_module_named_like_yfinance_shown(self):
        assert len(_shown(_INPLACE, "notyfinance.utils")) == 1

    def test_other_yfinance_pandas4_shown(self):
        assert len(_shown("Some other pandas 4 change", "yfinance.utils")) == 1
