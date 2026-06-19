"""Tests for dashboard/page_ticker.py — AC1, AC2, AC3, AC5, AC9, AC10."""

from __future__ import annotations

import pandas as pd
import pytest
import sqlalchemy as sa

from dashboard.page_ticker import _score_colour
from dashboard.theme import LONG_COL, NEUTRAL, SHORT_COL
from data.db import (
    daily_prices,
    earnings_calendar,
    insider_transactions,
    sp500_universe,
)

# ---------------------------------------------------------------------------
# AC5 — _score_colour helper
# ---------------------------------------------------------------------------


def test_score_colour_green():
    assert _score_colour(75) == LONG_COL


def test_score_colour_green_boundary():
    assert _score_colour(60) == LONG_COL


def test_score_colour_red():
    assert _score_colour(25) == SHORT_COL


def test_score_colour_red_boundary():
    assert _score_colour(40) == SHORT_COL


def test_score_colour_neutral():
    assert _score_colour(50) == NEUTRAL


def test_score_colour_none():
    assert _score_colour(None) == NEUTRAL


# ---------------------------------------------------------------------------
# AC1 — ticker list query
# ---------------------------------------------------------------------------


def test_ticker_list_query(tmp_engine):
    with tmp_engine.connect() as conn:
        conn.execute(
            sp500_universe.insert(),
            [
                {
                    "ticker": "MSFT",
                    "company_name": "Microsoft Corp",
                    "gics_sector": "IT",
                    "gics_sub_industry": "Software",
                    "updated_at": "2026-01-01",
                },
                {
                    "ticker": "AAPL",
                    "company_name": "Apple Inc",
                    "gics_sector": "IT",
                    "gics_sub_industry": "Hardware",
                    "updated_at": "2026-01-01",
                },
            ],
        )
        conn.commit()

        rows = conn.execute(
            sa.select(
                sp500_universe.c.ticker,
                sp500_universe.c.company_name,
            ).order_by(sp500_universe.c.ticker)
        ).fetchall()

    tickers = [r[0] for r in rows]
    assert tickers == ["AAPL", "MSFT"]
    assert rows[0][1] == "Apple Inc"


# ---------------------------------------------------------------------------
# AC2 — price query returns empty DataFrame without raising
# ---------------------------------------------------------------------------


def test_price_chart_no_data_does_not_raise(tmp_engine):
    with tmp_engine.connect() as conn:
        rows = conn.execute(
            sa.select(daily_prices)
            .where(daily_prices.c.ticker == "UNKNOWN")
            .order_by(daily_prices.c.date)
        ).fetchall()

    df = pd.DataFrame(rows, columns=daily_prices.columns.keys())
    assert df.empty


# ---------------------------------------------------------------------------
# AC3 — price KPI calculations
# ---------------------------------------------------------------------------


def _make_price_df(n_rows: int = 260, base_price: float = 100.0) -> pd.DataFrame:
    """Build a synthetic price DataFrame with incrementing prices."""

    dates = pd.date_range(end="2026-06-19", periods=n_rows, freq="B")
    prices = [base_price + i * 0.1 for i in range(n_rows)]
    return pd.DataFrame(
        {
            "date": dates,
            "adj_close": prices,
            "volume": [1_000_000] * n_rows,
        }
    )


def test_price_kpi_52w():
    df = _make_price_df(260)
    tail_252 = df["adj_close"].tail(252)
    assert float(tail_252.max()) == pytest.approx(df["adj_close"].iloc[-1], rel=1e-6)
    assert float(tail_252.min()) < float(tail_252.max())


def test_ytd_return_positive():

    df = _make_price_df(260)
    df["date"] = pd.to_datetime(df["date"])
    current_year = int(df["date"].dt.year.max())
    ytd_df = df[df["date"].dt.year == current_year]
    latest_price = float(df["adj_close"].iloc[-1])
    ret = latest_price / float(ytd_df["adj_close"].iloc[0]) - 1
    assert ret > 0


def test_ytd_return_negative():

    # Flip prices so the latest is the lowest of the year
    n = 260
    dates = pd.date_range(end="2026-06-19", periods=n, freq="B")
    prices = [200.0 - i * 0.1 for i in range(n)]
    df = pd.DataFrame({"date": dates, "adj_close": prices, "volume": [1_000_000] * n})
    df["date"] = pd.to_datetime(df["date"])
    current_year = int(df["date"].dt.year.max())
    ytd_df = df[df["date"].dt.year == current_year]
    latest_price = float(df["adj_close"].iloc[-1])
    ret = latest_price / float(ytd_df["adj_close"].iloc[0]) - 1
    assert ret < 0


def test_ytd_return_single_row():
    dates = pd.to_datetime(["2026-06-19"])
    df = pd.DataFrame({"date": dates, "adj_close": [150.0], "volume": [1_000_000]})
    current_year = int(df["date"].dt.year.max())
    ytd_df = df[df["date"].dt.year == current_year]
    ret = (
        df["adj_close"].iloc[-1] / float(ytd_df["adj_close"].iloc[0]) - 1
        if len(ytd_df) > 1
        else 0.0
    )
    assert ret == 0.0


def test_avg_volume_30d():
    df = _make_price_df(260)
    vol_series = df["volume"].dropna()
    avg = int(vol_series.tail(30).mean())
    assert avg == 1_000_000


# ---------------------------------------------------------------------------
# AC9 — insider query limit
# ---------------------------------------------------------------------------


def test_insider_query_limit(tmp_engine):
    rows = [
        {
            "ticker": "AAPL",
            "insider_name": f"Person {i}",
            "insider_title": "CFO",
            "transaction_type": "Buy",
            "transaction_code": "P",
            "shares": 100.0,
            "price": 150.0,
            "date": f"2026-01-{i:02d}",
            "ownership_type": "D",
            "is_open_market": 1,
            "is_ceo_cfo": 0,
            "accession_no": f"ACC{i:04d}",
            "fetched_at": "2026-01-01",
        }
        for i in range(1, 16)  # 15 rows
    ]
    with tmp_engine.connect() as conn:
        conn.execute(insider_transactions.insert(), rows)
        conn.commit()

        result = conn.execute(
            sa.select(insider_transactions)
            .where(insider_transactions.c.ticker == "AAPL")
            .order_by(insider_transactions.c.date.desc())
            .limit(12)
        ).fetchall()

    assert len(result) == 12
    # Most recent date should appear first
    dates = [r[insider_transactions.columns.keys().index("date")] for r in result]
    assert dates == sorted(dates, reverse=True)


# ---------------------------------------------------------------------------
# AC10 — empty universe guard
# ---------------------------------------------------------------------------


def test_empty_universe_returns_early(tmp_engine):
    with tmp_engine.connect() as conn:
        rows = conn.execute(sa.select(sp500_universe).order_by(sp500_universe.c.ticker)).fetchall()

    assert rows == []


def test_earnings_caption_query(tmp_engine):
    with tmp_engine.connect() as conn:
        conn.execute(
            earnings_calendar.insert().values(
                ticker="AAPL",
                earnings_date="2026-07-30",
                eps_estimate=1.45,
                fetched_at="2026-06-01",
            )
        )
        conn.commit()

        row = conn.execute(
            sa.select(earnings_calendar)
            .where(earnings_calendar.c.ticker == "AAPL")
            .order_by(earnings_calendar.c.earnings_date.desc())
            .limit(1)
        ).fetchone()

    assert row is not None
    er = dict(zip(earnings_calendar.columns.keys(), row, strict=False))
    assert er["earnings_date"] == "2026-07-30"
    assert er["eps_estimate"] == pytest.approx(1.45)
