"""Tests for factors/loader.py."""

import sys
from pathlib import Path
from datetime import date, timedelta

import pandas as pd
import pytest
import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).parent.parent))

import factors.db  # noqa: F401 — registers Layer 2 tables on metadata
from factors.loader import load_scoring_data
from data.db import (
    sp500_universe, daily_prices, fundamentals,
    short_interest, analyst_estimates,
    insider_transactions, insider_cluster_flags,
    institutional_summary,
)


SCORE_DATE = "2024-06-01"


def _insert_universe(conn, tickers_sectors):
    for ticker, sector in tickers_sectors:
        conn.execute(sp500_universe.insert().values(
            ticker=ticker, company_name=ticker, gics_sector=sector,
            gics_sub_industry=sector, updated_at="2024-01-01",
        ))
    conn.commit()


def _insert_prices(conn, ticker, dates_closes, is_vix=False):
    t = ticker if is_vix else ticker
    for d, c in dates_closes:
        conn.execute(daily_prices.insert().values(
            ticker=t, date=d, open=c, high=c, low=c,
            close=c, adj_close=c, volume=1000,
        ))
    conn.commit()


def _insert_fundamentals(conn, ticker, period_end, **kwargs):
    row = dict(
        ticker=ticker, period_type="quarterly", period_end=period_end,
        revenue=1e9, gross_profit=4e8, operating_income=2e8, ebit=2e8,
        net_income=1e8, rd_expense=0, total_assets=5e9, total_liabilities=2e9,
        total_equity=3e9, cash=5e8, total_debt=1e9, current_assets=1e9,
        current_liabilities=5e8, accounts_receivable=2e8, retained_earnings=1e9,
        shares_outstanding=1e8, dividends_paid=-1e7, cfo=1.5e8, capex=-5e7,
        fcf=1e8, buybacks=-2e7, roe=0.10, roa=0.05, gross_margin=0.40,
        operating_margin=0.20, net_margin=0.10, revenue_growth_yoy=0.05,
        revenue_growth_qoq=0.01, earnings_growth_yoy=0.05, earnings_growth_qoq=0.01,
        debt_to_equity=0.33, current_ratio=2.0, ar_to_revenue=0.20,
        cfo_to_ni=1.5, accruals_ratio=0.01, working_capital=5e8, asset_turnover=0.20,
        updated_at="2024-01-01",
    )
    row.update(kwargs)
    conn.execute(fundamentals.insert().values(**row))
    conn.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLoadUniverse:
    def test_returns_dataframe_with_expected_columns(self, tmp_db):
        _insert_universe(tmp_db, [("AAPL", "Information Technology")])
        data = load_scoring_data(tmp_db, {}, SCORE_DATE)
        u = data["universe"]
        assert set(u.columns) >= {"ticker", "sector"}

    def test_all_tickers_returned(self, tmp_db):
        _insert_universe(tmp_db, [
            ("AAPL", "Information Technology"),
            ("JPM",  "Financials"),
        ])
        data = load_scoring_data(tmp_db, {}, SCORE_DATE)
        assert set(data["universe"]["ticker"]) == {"AAPL", "JPM"}

    def test_empty_universe(self, tmp_db):
        data = load_scoring_data(tmp_db, {}, SCORE_DATE)
        assert len(data["universe"]) == 0


class TestLoadPrices:
    def test_prices_within_window(self, tmp_db):
        _insert_universe(tmp_db, [("AAPL", "Tech")])
        _insert_prices(tmp_db, "AAPL", [
            ("2024-05-01", 100.0),
            ("2024-06-01", 110.0),
        ])
        data = load_scoring_data(tmp_db, {}, SCORE_DATE)
        assert len(data["prices"]) == 2

    def test_prices_after_score_date_excluded(self, tmp_db):
        _insert_universe(tmp_db, [("AAPL", "Tech")])
        _insert_prices(tmp_db, "AAPL", [
            ("2024-06-01", 110.0),
            ("2024-06-15", 120.0),   # after score_date
        ])
        data = load_scoring_data(tmp_db, {}, SCORE_DATE)
        assert all(data["prices"]["date"] <= pd.Timestamp(SCORE_DATE))

    def test_prices_date_parsed_as_timestamp(self, tmp_db):
        _insert_universe(tmp_db, [("AAPL", "Tech")])
        _insert_prices(tmp_db, "AAPL", [("2024-06-01", 100.0)])
        data = load_scoring_data(tmp_db, {}, SCORE_DATE)
        assert pd.api.types.is_datetime64_any_dtype(data["prices"]["date"])


class TestLoadFundamentals:
    def test_only_quarterly_rows_returned(self, tmp_db):
        _insert_universe(tmp_db, [("AAPL", "Tech")])
        _insert_fundamentals(tmp_db, "AAPL", "2024-03-31")
        # Insert an annual row manually
        conn = tmp_db
        conn.execute(fundamentals.insert().values(
            ticker="AAPL", period_type="annual", period_end="2024-03-31",
            revenue=1e10, updated_at="2024-01-01",
        ))
        conn.commit()
        data = load_scoring_data(tmp_db, {}, SCORE_DATE)
        assert all(data["fundamentals"]["period_type"] == "quarterly")

    def test_periods_after_score_date_excluded(self, tmp_db):
        _insert_universe(tmp_db, [("AAPL", "Tech")])
        _insert_fundamentals(tmp_db, "AAPL", "2024-09-30")  # after score_date
        data = load_scoring_data(tmp_db, {}, SCORE_DATE)
        assert len(data["fundamentals"]) == 0


class TestLoadInsider:
    def test_non_open_market_filtered_out(self, tmp_db):
        _insert_universe(tmp_db, [("AAPL", "Tech")])
        tmp_db.execute(insider_transactions.insert().values(
            ticker="AAPL", insider_name="CEO", insider_title="CEO",
            transaction_type="Buy", transaction_code="P",
            shares=100, price=150.0, date="2024-05-01",
            ownership_type="D", is_open_market=0, is_ceo_cfo=1,
            accession_no="0001", fetched_at="2024-06-01",
        ))
        tmp_db.commit()
        data = load_scoring_data(tmp_db, {}, SCORE_DATE)
        assert len(data["insider_txns"]) == 0

    def test_open_market_transaction_included(self, tmp_db):
        _insert_universe(tmp_db, [("AAPL", "Tech")])
        tmp_db.execute(insider_transactions.insert().values(
            ticker="AAPL", insider_name="CEO", insider_title="CEO",
            transaction_type="Buy", transaction_code="P",
            shares=100, price=150.0, date="2024-05-01",
            ownership_type="D", is_open_market=1, is_ceo_cfo=1,
            accession_no="0001", fetched_at="2024-06-01",
        ))
        tmp_db.commit()
        data = load_scoring_data(tmp_db, {}, SCORE_DATE)
        assert len(data["insider_txns"]) == 1


class TestLoadVix:
    def test_vix_loaded(self, tmp_db):
        _insert_prices(tmp_db, "^VIX", [("2024-06-01", 18.5)])
        data = load_scoring_data(tmp_db, {}, SCORE_DATE)
        assert len(data["vix"]) == 1
        assert data["vix"].iloc[0]["close"] == pytest.approx(18.5)

    def test_vix_empty_when_no_data(self, tmp_db):
        data = load_scoring_data(tmp_db, {}, SCORE_DATE)
        assert len(data["vix"]) == 0
