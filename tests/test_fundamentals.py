"""Fundamentals — ratio calculations, DB round-trips, FMP field mapping."""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
import sqlalchemy as sa

from data.db import fundamentals as fundamentals_table
from data.db import insert_or_replace
from data.fundamentals import (
    _compute_ratios,
    _fmp_raw,
    _is_fresh,
    _pct_change,
    _safe,
    _upsert_period,
    update_fundamentals,
)
from data.providers import FundamentalsProvider, Providers

# ---------------------------------------------------------------------------
# _safe
# ---------------------------------------------------------------------------


class TestSafe:
    def test_float_passthrough(self):
        assert _safe(3.14) == pytest.approx(3.14)

    def test_integer(self):
        assert _safe(42) == pytest.approx(42.0)

    def test_string_number(self):
        assert _safe("1.5") == pytest.approx(1.5)

    def test_none_returns_none(self):
        assert _safe(None) is None

    def test_nan_returns_none(self):
        assert _safe(float("nan")) is None

    def test_non_numeric_returns_none(self):
        assert _safe("N/A") is None

    def test_zero_is_valid(self):
        assert _safe(0) == pytest.approx(0.0)

    def test_negative(self):
        assert _safe(-100.5) == pytest.approx(-100.5)


# ---------------------------------------------------------------------------
# _pct_change
# ---------------------------------------------------------------------------


class TestPctChange:
    def test_basic(self):
        assert _pct_change(110.0, 100.0) == pytest.approx(0.10)

    def test_decline(self):
        assert _pct_change(90.0, 100.0) == pytest.approx(-0.10)

    def test_current_none_returns_none(self):
        assert _pct_change(None, 100.0) is None

    def test_prior_none_returns_none(self):
        assert _pct_change(100.0, None) is None

    def test_both_none_returns_none(self):
        assert _pct_change(None, None) is None

    def test_zero_prior_returns_none(self):
        assert _pct_change(100.0, 0.0) is None

    def test_negative_prior_uses_absolute_value(self):
        # (10 - (-100)) / abs(-100) = 110 / 100 = 1.1
        assert _pct_change(10.0, -100.0) == pytest.approx(1.1)


# ---------------------------------------------------------------------------
# _compute_ratios
# ---------------------------------------------------------------------------


@pytest.fixture
def base_period():
    return {
        "revenue": 100.0,
        "gross_profit": 40.0,
        "operating_income": 20.0,
        "net_income": 15.0,
        "total_equity": 60.0,
        "total_assets": 200.0,
        "total_debt": 30.0,
        "current_assets": 60.0,
        "current_liabilities": 30.0,
        "accounts_receivable": 10.0,
        "cfo": 20.0,
        "fcf": 11.0,
    }


class TestComputeRatios:
    def test_gross_margin(self, base_period):
        r = _compute_ratios(base_period, None, None)
        assert r["gross_margin"] == pytest.approx(0.40)

    def test_net_margin(self, base_period):
        r = _compute_ratios(base_period, None, None)
        assert r["net_margin"] == pytest.approx(0.15)

    def test_operating_margin(self, base_period):
        r = _compute_ratios(base_period, None, None)
        assert r["operating_margin"] == pytest.approx(0.20)

    def test_roa(self, base_period):
        r = _compute_ratios(base_period, None, None)
        assert r["roa"] == pytest.approx(15.0 / 200.0)

    def test_debt_to_equity(self, base_period):
        r = _compute_ratios(base_period, None, None)
        assert r["debt_to_equity"] == pytest.approx(30.0 / 60.0)

    def test_current_ratio(self, base_period):
        r = _compute_ratios(base_period, None, None)
        assert r["current_ratio"] == pytest.approx(2.0)

    def test_working_capital(self, base_period):
        r = _compute_ratios(base_period, None, None)
        assert r["working_capital"] == pytest.approx(30.0)

    def test_ar_to_revenue(self, base_period):
        r = _compute_ratios(base_period, None, None)
        assert r["ar_to_revenue"] == pytest.approx(0.10)

    def test_cfo_to_ni(self, base_period):
        r = _compute_ratios(base_period, None, None)
        assert r["cfo_to_ni"] == pytest.approx(20.0 / 15.0)

    def test_asset_turnover(self, base_period):
        r = _compute_ratios(base_period, None, None)
        assert r["asset_turnover"] == pytest.approx(100.0 / 200.0)

    def test_accruals_ratio(self, base_period):
        # (NI - CFO) / assets = (15 - 20) / 200 = -0.025
        r = _compute_ratios(base_period, None, None)
        assert r["accruals_ratio"] == pytest.approx(-0.025)

    def test_no_prior_growth_is_none(self, base_period):
        r = _compute_ratios(base_period, None, None)
        assert r["revenue_growth_qoq"] is None
        assert r["revenue_growth_yoy"] is None
        assert r["earnings_growth_qoq"] is None
        assert r["earnings_growth_yoy"] is None

    def test_qoq_growth(self, base_period):
        prev = {"revenue": 80.0, "net_income": 10.0}
        r = _compute_ratios(base_period, prev, None)
        assert r["revenue_growth_qoq"] == pytest.approx(0.25)
        assert r["earnings_growth_qoq"] == pytest.approx(0.50)

    def test_yoy_growth(self, base_period):
        prev_yoy = {"revenue": 90.0, "net_income": 12.0}
        r = _compute_ratios(base_period, None, prev_yoy)
        assert r["revenue_growth_yoy"] == pytest.approx(10.0 / 90.0)
        assert r["earnings_growth_yoy"] == pytest.approx(3.0 / 12.0)

    def test_zero_revenue_margins_are_none(self, base_period):
        base_period["revenue"] = 0.0
        r = _compute_ratios(base_period, None, None)
        assert r["gross_margin"] is None
        assert r["net_margin"] is None
        assert r["operating_margin"] is None

    def test_none_values_propagate_safely(self):
        empty = dict.fromkeys(
            [
                "revenue",
                "gross_profit",
                "operating_income",
                "net_income",
                "total_equity",
                "total_assets",
                "total_debt",
                "current_assets",
                "current_liabilities",
                "accounts_receivable",
                "cfo",
                "fcf",
            ]
        )
        r = _compute_ratios(empty, None, None)
        for key, val in r.items():
            assert val is None, f"{key} should be None when inputs are None"


# ---------------------------------------------------------------------------
# _upsert_period — DB round-trip
# ---------------------------------------------------------------------------


class TestUpsertPeriod:
    def test_stores_and_retrieves_row(self, tmp_db):
        raw = {
            "revenue": 1e9,
            "gross_profit": 4e8,
            "operating_income": 2e8,
            "ebit": 2e8,
            "net_income": 1.5e8,
            "rd_expense": 1e7,
            "total_assets": 5e9,
            "total_liabilities": 3e9,
            "total_equity": 2e9,
            "cash": 5e8,
            "total_debt": 1e9,
            "current_assets": 1.2e9,
            "current_liabilities": 6e8,
            "accounts_receivable": 2e8,
            "retained_earnings": 8e8,
            "shares_outstanding": 1e8,
            "dividends_paid": -5e7,
            "cfo": 2.5e8,
            "capex": -5e7,
            "fcf": 2e8,
            "buybacks": -1e8,
        }
        ratios = _compute_ratios(raw, None, None)
        _upsert_period(tmp_db, "AAPL", "quarterly", "2024-09-30", raw, ratios)
        tmp_db.commit()

        row = tmp_db.execute(
            sa.select(
                fundamentals_table.c.revenue,
                fundamentals_table.c.gross_margin,
                fundamentals_table.c.net_margin,
            ).where(
                (fundamentals_table.c.ticker == "AAPL")
                & (fundamentals_table.c.period_type == "quarterly")
                & (fundamentals_table.c.period_end == "2024-09-30")
            )
        ).fetchone()
        assert row is not None
        assert row[0] == pytest.approx(1e9)
        assert row[1] == pytest.approx(0.40)
        assert row[2] == pytest.approx(0.15)

    def test_upsert_replaces_existing(self, tmp_db):
        raw = dict.fromkeys(
            [
                "revenue",
                "gross_profit",
                "operating_income",
                "ebit",
                "net_income",
                "rd_expense",
                "total_assets",
                "total_liabilities",
                "total_equity",
                "cash",
                "total_debt",
                "current_assets",
                "current_liabilities",
                "accounts_receivable",
                "retained_earnings",
                "shares_outstanding",
                "dividends_paid",
                "cfo",
                "capex",
                "fcf",
                "buybacks",
            ]
        )
        raw["revenue"] = 100.0
        ratios = _compute_ratios(raw, None, None)
        _upsert_period(tmp_db, "MSFT", "annual", "2024-06-30", raw, ratios)

        raw["revenue"] = 999.0
        _upsert_period(tmp_db, "MSFT", "annual", "2024-06-30", raw, ratios)
        tmp_db.commit()

        count = tmp_db.execute(
            sa.select(sa.func.count())
            .select_from(fundamentals_table)
            .where(fundamentals_table.c.ticker == "MSFT")
        ).scalar()
        assert count == 1

        revenue = tmp_db.execute(
            sa.select(fundamentals_table.c.revenue).where(fundamentals_table.c.ticker == "MSFT")
        ).scalar()
        assert revenue == pytest.approx(999.0)


# ---------------------------------------------------------------------------
# _fmp_raw — field mapping
# ---------------------------------------------------------------------------


class TestFmpRaw:
    def _sample(self):
        inc = {
            "revenue": 100_000,
            "grossProfit": 40_000,
            "operatingIncome": 20_000,
            "netIncome": 15_000,
            "researchAndDevelopmentExpenses": 5_000,
            "weightedAverageShsOut": 1_000_000,
        }
        bal = {
            "totalAssets": 500_000,
            "totalLiabilities": 300_000,
            "totalStockholdersEquity": 200_000,
            "cashAndCashEquivalents": 50_000,
            "totalDebt": 100_000,
            "totalCurrentAssets": 120_000,
            "totalCurrentLiabilities": 60_000,
            "netReceivables": 20_000,
            "retainedEarnings": 80_000,
        }
        cf = {
            "operatingCashFlow": 25_000,
            "capitalExpenditure": -5_000,
            "freeCashFlow": 20_000,
            "commonStockRepurchased": -10_000,
            "commonDividendsPaid": -3_000,
        }
        return inc, bal, cf

    def test_basic_mapping(self):
        inc, bal, cf = self._sample()
        raw = _fmp_raw(inc, bal, cf)
        assert raw["revenue"] == pytest.approx(100_000)
        assert raw["gross_profit"] == pytest.approx(40_000)
        assert raw["net_income"] == pytest.approx(15_000)
        assert raw["total_assets"] == pytest.approx(500_000)
        assert raw["cfo"] == pytest.approx(25_000)
        assert raw["fcf"] == pytest.approx(20_000)

    def test_fcf_computed_when_missing(self):
        inc, bal, cf = self._sample()
        del cf["freeCashFlow"]
        raw = _fmp_raw(inc, bal, cf)
        # FCF = CFO - abs(capex) = 25_000 - 5_000 = 20_000
        assert raw["fcf"] == pytest.approx(20_000)

    def test_dividends_paid_fallback(self):
        inc, bal, cf = self._sample()
        del cf["commonDividendsPaid"]
        cf["netDividendsPaid"] = -4_000
        raw = _fmp_raw(inc, bal, cf)
        assert raw["dividends_paid"] == pytest.approx(-4_000)

    def test_empty_statements_return_nones(self):
        raw = _fmp_raw({}, {}, {})
        assert raw["revenue"] is None
        assert raw["net_income"] is None
        assert raw["total_assets"] is None
        assert raw["fcf"] is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _empty_raw():
    return dict.fromkeys(
        [
            "revenue",
            "gross_profit",
            "operating_income",
            "ebit",
            "net_income",
            "rd_expense",
            "total_assets",
            "total_liabilities",
            "total_equity",
            "cash",
            "total_debt",
            "current_assets",
            "current_liabilities",
            "accounts_receivable",
            "retained_earnings",
            "shares_outstanding",
            "dividends_paid",
            "cfo",
            "capex",
            "fcf",
            "buybacks",
        ]
    )


def _insert_with_timestamp(conn, ticker, ts_str):
    raw = _empty_raw()
    ratios = _compute_ratios(raw, None, None)
    conn.execute(
        insert_or_replace(conn, fundamentals_table).values(
            ticker=ticker,
            period_type="quarterly",
            period_end="2024-09-30",
            updated_at=ts_str,
            **raw,
            **ratios,
        )
    )
    conn.commit()


def _make_providers(provider: FundamentalsProvider, fmp_key: str = "") -> Providers:
    p = object.__new__(Providers)
    p.fundamentals = provider
    p.fmp_key = fmp_key
    return p


# ---------------------------------------------------------------------------
# _is_fresh
# ---------------------------------------------------------------------------


class TestIsFresh:
    def test_no_data_returns_false(self, tmp_db):
        assert not _is_fresh(tmp_db, "AAPL", 7)

    def test_recent_data_returns_true(self, tmp_db):
        raw = _empty_raw()
        _upsert_period(
            tmp_db, "AAPL", "quarterly", "2024-09-30", raw, _compute_ratios(raw, None, None)
        )
        tmp_db.commit()
        assert _is_fresh(tmp_db, "AAPL", 7)

    def test_stale_data_returns_false(self, tmp_db):
        old_ts = (datetime.utcnow() - timedelta(days=10)).isoformat(timespec="seconds")
        _insert_with_timestamp(tmp_db, "AAPL", old_ts)
        assert not _is_fresh(tmp_db, "AAPL", 7)

    def test_boundary_within_one_day_is_fresh(self, tmp_db):
        raw = _empty_raw()
        _upsert_period(
            tmp_db, "AAPL", "quarterly", "2024-09-30", raw, _compute_ratios(raw, None, None)
        )
        tmp_db.commit()
        assert _is_fresh(tmp_db, "AAPL", 1)

    def test_boundary_just_expired(self, tmp_db):
        old_ts = (datetime.utcnow() - timedelta(days=8)).isoformat(timespec="seconds")
        _insert_with_timestamp(tmp_db, "AAPL", old_ts)
        assert not _is_fresh(tmp_db, "AAPL", 7)

    def test_different_tickers_are_independent(self, tmp_db):
        raw = _empty_raw()
        _upsert_period(
            tmp_db, "AAPL", "quarterly", "2024-09-30", raw, _compute_ratios(raw, None, None)
        )
        tmp_db.commit()
        assert _is_fresh(tmp_db, "AAPL", 7)
        assert not _is_fresh(tmp_db, "MSFT", 7)


# ---------------------------------------------------------------------------
# update_fundamentals — staleness skip logic
# ---------------------------------------------------------------------------


class TestUpdateFundamentalsSkip:
    def test_fresh_ticker_skipped(self, tmp_db, config):
        raw = _empty_raw()
        _upsert_period(
            tmp_db, "AAPL", "quarterly", "2024-09-30", raw, _compute_ratios(raw, None, None)
        )
        tmp_db.commit()
        config["fundamentals"]["refresh_days"] = 7
        providers = _make_providers(FundamentalsProvider.YFINANCE)

        with patch("data.fundamentals._process_ticker_yfinance") as mock_proc:
            result = update_fundamentals(tmp_db, ["AAPL"], config, providers)

        mock_proc.assert_not_called()
        assert result["AAPL"] == 0

    def test_stale_ticker_fetched(self, tmp_db, config):
        old_ts = (datetime.utcnow() - timedelta(days=10)).isoformat(timespec="seconds")
        _insert_with_timestamp(tmp_db, "MSFT", old_ts)
        config["fundamentals"]["refresh_days"] = 7
        providers = _make_providers(FundamentalsProvider.YFINANCE)

        with patch("data.fundamentals._process_ticker_yfinance", return_value=8) as mock_proc:
            result = update_fundamentals(tmp_db, ["MSFT"], config, providers)

        mock_proc.assert_called_once_with(tmp_db, "MSFT")
        assert result["MSFT"] == 8

    def test_new_ticker_fetched(self, tmp_db, config):
        config["fundamentals"]["refresh_days"] = 7
        providers = _make_providers(FundamentalsProvider.YFINANCE)

        with patch("data.fundamentals._process_ticker_yfinance", return_value=12) as mock_proc:
            result = update_fundamentals(tmp_db, ["NVDA"], config, providers)

        mock_proc.assert_called_once_with(tmp_db, "NVDA")
        assert result["NVDA"] == 12

    def test_mixed_fresh_and_stale(self, tmp_db, config):
        raw = _empty_raw()
        _upsert_period(
            tmp_db, "AAPL", "quarterly", "2024-09-30", raw, _compute_ratios(raw, None, None)
        )
        old_ts = (datetime.utcnow() - timedelta(days=10)).isoformat(timespec="seconds")
        _insert_with_timestamp(tmp_db, "MSFT", old_ts)
        tmp_db.commit()
        config["fundamentals"]["refresh_days"] = 7
        providers = _make_providers(FundamentalsProvider.YFINANCE)

        with patch("data.fundamentals._process_ticker_yfinance", return_value=8) as mock_proc:
            result = update_fundamentals(tmp_db, ["AAPL", "MSFT"], config, providers)

        mock_proc.assert_called_once_with(tmp_db, "MSFT")
        assert result["AAPL"] == 0
        assert result["MSFT"] == 8

    def test_fmp_provider_uses_fmp_processor(self, tmp_db, config):
        config["fundamentals"]["refresh_days"] = 7
        providers = _make_providers(FundamentalsProvider.FMP, fmp_key="test-key")

        with patch("data.fundamentals._process_ticker_fmp", return_value=5) as mock_proc:
            result = update_fundamentals(tmp_db, ["TSLA"], config, providers)

        mock_proc.assert_called_once_with(tmp_db, "TSLA", "test-key")
        assert result["TSLA"] == 5

    def test_all_fresh_returns_early(self, tmp_db, config):
        raw = _empty_raw()
        for ticker in ["AAPL", "MSFT", "GOOG"]:
            _upsert_period(
                tmp_db, ticker, "quarterly", "2024-09-30", raw, _compute_ratios(raw, None, None)
            )
        tmp_db.commit()
        config["fundamentals"]["refresh_days"] = 7
        providers = _make_providers(FundamentalsProvider.YFINANCE)

        with patch("data.fundamentals._process_ticker_yfinance") as mock_proc:
            result = update_fundamentals(tmp_db, ["AAPL", "MSFT", "GOOG"], config, providers)

        mock_proc.assert_not_called()
        assert all(v == 0 for v in result.values())


# ---------------------------------------------------------------------------
# Packet 23 — shares, statement alignment, month-end periods, duplicate merge
# ---------------------------------------------------------------------------

import pandas as pd  # noqa: E402

from data import fundamentals as fund_mod  # noqa: E402


def _stmt(rows: dict[str, list], dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame(rows, index=pd.to_datetime(dates)).T


class TestSharesOutstanding:
    def _shares(self, rows):
        bal = _stmt(rows, ["2026-06-30"])
        return fund_mod._extract_statement_data(None, bal, None, 0)["shares_outstanding"]

    def test_ordinary_shares_preferred(self):
        # JPM, 2026-10-14: issued 4.11B, outstanding 2.66B, treasury 1.45B
        rows = {
            "Share Issued": [4_105_308_672.0],
            "Ordinary Shares Number": [2_658_186_195.0],
            "Treasury Shares Number": [1_447_122_477.0],
        }
        assert self._shares(rows) == pytest.approx(2_658_186_195)

    def test_issued_minus_treasury(self):
        rows = {"Share Issued": [4_105_308_672.0], "Treasury Shares Number": [1_447_122_477.0]}
        assert self._shares(rows) == pytest.approx(2_658_186_195)

    def test_issued_alone(self):
        assert self._shares({"Share Issued": [14_687_356_000.0]}) == pytest.approx(14_687_356_000)

    def test_nan_ordinary_falls_through(self):
        # Adversarial: a present-but-NaN row must not end the search
        rows = {"Ordinary Shares Number": [float("nan")], "Share Issued": [500.0]}
        assert self._shares(rows) == pytest.approx(500)

    def test_treasury_without_issued_uses_last_key(self):
        rows = {"Treasury Shares Number": [10.0], "Common Stock Shares Outstanding": [90.0]}
        assert self._shares(rows) == pytest.approx(90)


class TestAlignment:
    def test_balance_missing_latest_quarter_not_shifted(self):
        # BAC, 2026-10-14: income has 2026-06-30, balance sheet and cash flow start at 2026-03-31
        inc = _stmt({"Net Income": [7.0, 6.0, 5.0]}, ["2026-06-30", "2026-03-31", "2025-12-31"])
        bal = _stmt({"Total Debt": [60.0, 50.0]}, ["2026-03-31", "2025-12-31"])
        cf = _stmt({"Operating Cash Flow": [600.0, 500.0]}, ["2026-03-31", "2025-12-31"])
        cols = inc.columns
        bal, cf = fund_mod._align(bal, cols), fund_mod._align(cf, cols)
        rows = [fund_mod._extract_statement_data(inc, bal, cf, i) for i in range(3)]
        assert [r["total_debt"] for r in rows] == [None, 60.0, 50.0]
        assert [r["cfo"] for r in rows] == [None, 600.0, 500.0]

    def test_income_gap_not_shifted(self):
        # WMT: income skips 2025-04-30; balance sheet has it
        inc = _stmt({"Net Income": [3.0, 2.0, 1.0]}, ["2025-10-31", "2025-07-31", "2025-01-31"])
        bal = _stmt(
            {"Total Debt": [40.0, 30.0, 20.0, 10.0]},
            ["2025-10-31", "2025-07-31", "2025-04-30", "2025-01-31"],
        )
        bal = fund_mod._align(bal, inc.columns)
        assert [
            fund_mod._extract_statement_data(inc, bal, None, i)["total_debt"] for i in range(3)
        ] == [
            40.0,
            30.0,
            10.0,
        ]

    def test_dates_matched_by_month_end(self):
        # Adversarial: 52/53-week date on one statement, month-end on another
        inc = _stmt({"Net Income": [1.0]}, ["2025-09-27"])
        bal = _stmt({"Total Debt": [9.0]}, ["2025-09-30"])
        assert fund_mod._align(bal, inc.columns).iloc[0, 0] == pytest.approx(9.0)

    def test_empty_statement(self):
        inc = _stmt({"Net Income": [1.0]}, ["2025-09-30"])
        assert fund_mod._align(pd.DataFrame(), inc.columns).empty


class TestMonthEnd:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("2025-09-27", "2025-09-30"),
            ("2025-09-30", "2025-09-30"),
            ("2026-01-02", "2025-12-31"),
            ("2024-02-26", "2024-02-29"),
            ("2025-11-28", "2025-11-30"),
            ("2025-11-28T00:00:00", "2025-11-30"),
        ],
    )
    def test_nearest_month_end(self, raw, expected):
        assert fund_mod.month_end(raw) == expected

    def test_upsert_stores_month_end(self, tmp_db):
        raw = _empty_raw()
        _upsert_period(
            tmp_db, "AAPL", "annual", "2025-09-27", raw, _compute_ratios(raw, None, None)
        )
        tmp_db.commit()
        ends = tmp_db.execute(sa.select(fundamentals_table.c.period_end)).scalars().all()
        assert ends == ["2025-09-30"]


def _put(conn, ticker, ptype, end, updated, debt):
    raw = _empty_raw()
    raw["total_debt"] = debt
    conn.execute(
        insert_or_replace(conn, fundamentals_table).values(
            ticker=ticker,
            period_type=ptype,
            period_end=end,
            updated_at=updated,
            **raw,
            **_compute_ratios(raw, None, None),
        )
    )


class TestMergePeriodDuplicates:
    def test_newest_kept_and_redated(self, tmp_db, caplog):
        # AAPL FY2025 as stored on 2026-10-14
        _put(tmp_db, "AAPL", "annual", "2025-09-27", "2026-05-03T20:05:57", 112_377e6)
        _put(tmp_db, "AAPL", "annual", "2025-09-30", "2026-10-14T09:58:06", 98_657e6)
        _put(tmp_db, "AMD", "annual", "2024-12-28", "2026-05-03T20:04:40", 1.0)
        _put(tmp_db, "AAPL", "quarterly", "2025-09-30", "2026-10-14T09:58:06", 5.0)
        tmp_db.commit()

        with caplog.at_level("WARNING", logger="data.fundamentals"):
            assert fund_mod.merge_period_duplicates(tmp_db) == 1
        rows = tmp_db.execute(
            sa.select(
                fundamentals_table.c.ticker,
                fundamentals_table.c.period_type,
                fundamentals_table.c.period_end,
                fundamentals_table.c.total_debt,
            ).order_by(fundamentals_table.c.ticker, fundamentals_table.c.period_type)
        ).fetchall()
        assert [tuple(r) for r in rows] == [
            ("AAPL", "annual", "2025-09-30", 98_657e6),
            ("AAPL", "quarterly", "2025-09-30", 5.0),
            ("AMD", "annual", "2024-12-31", 1.0),  # single row re-dated
        ]
        assert "Merged 1 duplicate fundamentals rows" in caplog.text

    def test_newest_wins_even_if_older_date_is_newer_fetch(self, tmp_db):
        # ADBE: the exact-date row is the newer fetch here
        _put(tmp_db, "ADBE", "annual", "2025-11-30", "2026-05-03T20:04:37", 1.0)
        _put(tmp_db, "ADBE", "annual", "2025-11-28", "2026-10-14T09:58:09", 2.0)
        tmp_db.commit()
        fund_mod.merge_period_duplicates(tmp_db)
        rows = tmp_db.execute(
            sa.select(fundamentals_table.c.period_end, fundamentals_table.c.total_debt)
        ).fetchall()
        assert [tuple(r) for r in rows] == [("2025-11-30", 2.0)]

    def test_idempotent(self, tmp_db):
        _put(tmp_db, "AAPL", "annual", "2025-09-27", "2026-05-03T20:05:57", 1.0)
        _put(tmp_db, "AAPL", "annual", "2025-09-30", "2026-10-14T09:58:06", 2.0)
        tmp_db.commit()
        fund_mod.merge_period_duplicates(tmp_db)
        assert fund_mod.merge_period_duplicates(tmp_db) == 0

    def test_update_runs_merge_first(self, tmp_db, config):
        _put(tmp_db, "AAPL", "annual", "2025-09-27", "2026-05-03T20:05:57", 1.0)
        _put(tmp_db, "AAPL", "annual", "2025-09-30", datetime.utcnow().isoformat(), 2.0)
        tmp_db.commit()
        update_fundamentals(
            tmp_db, ["AAPL"], config, _make_providers(FundamentalsProvider.YFINANCE)
        )
        n = tmp_db.execute(sa.select(sa.func.count()).select_from(fundamentals_table)).scalar()
        assert n == 1


class TestRefresh:
    def test_refresh_fetches_fresh_tickers(self, tmp_db, config):
        raw = _empty_raw()
        _upsert_period(
            tmp_db, "AAPL", "quarterly", "2024-09-30", raw, _compute_ratios(raw, None, None)
        )
        tmp_db.commit()
        config["fundamentals"]["refresh_days"] = 7
        providers = _make_providers(FundamentalsProvider.YFINANCE)
        with patch("data.fundamentals._process_ticker_yfinance", return_value=3) as mock_proc:
            result = update_fundamentals(tmp_db, ["AAPL"], config, providers, refresh=True)
        mock_proc.assert_called_once_with(tmp_db, "AAPL")
        assert result["AAPL"] == 3

    def test_cli_flag(self, monkeypatch):
        import run_data

        monkeypatch.setattr("sys.argv", ["run_data.py", "--refresh-fundamentals"])
        assert run_data._parse_args().refresh_fundamentals is True
        monkeypatch.setattr("sys.argv", ["run_data.py"])
        assert run_data._parse_args().refresh_fundamentals is False


class TestProcessTickerAlignment:
    def test_bac_shaped_statements_stored_by_period(self, tmp_db):
        # Wiring test: ingestion itself must use the aligned statements
        dates = ["2026-06-30", "2026-03-31", "2025-12-31"]
        inc = _stmt({"Net Income": [7.0, 6.0, 5.0], "Total Revenue": [70.0, 60.0, 50.0]}, dates)
        bal = _stmt({"Total Debt": [60.0, 50.0], "Ordinary Shares Number": [2.0, 3.0]}, dates[1:])
        cf = _stmt({"Operating Cash Flow": [600.0, 500.0]}, dates[1:])
        empty = pd.DataFrame()

        class FakeTicker:
            quarterly_income_stmt, quarterly_balance_sheet, quarterly_cashflow = inc, bal, cf
            income_stmt = balance_sheet = cashflow = empty

        with patch("data.fundamentals.yf.Ticker", return_value=FakeTicker()):
            assert fund_mod._process_ticker_yfinance(tmp_db, "BAC") == 3
        rows = tmp_db.execute(
            sa.select(
                fundamentals_table.c.period_end,
                fundamentals_table.c.total_debt,
                fundamentals_table.c.cfo,
                fundamentals_table.c.shares_outstanding,
            ).order_by(fundamentals_table.c.period_end.desc())
        ).fetchall()
        assert [tuple(r) for r in rows] == [
            ("2026-06-30", None, None, None),
            ("2026-03-31", 60.0, 600.0, 2.0),
            ("2025-12-31", 50.0, 500.0, 3.0),
        ]

    def test_newest_column_wins_shared_month(self):
        inc = _stmt({"Net Income": [1.0]}, ["2025-09-30"])
        bal = _stmt({"Total Debt": [9.0, 8.0]}, ["2025-09-30", "2025-09-27"])
        assert fund_mod._align(bal, inc.columns).iloc[0, 0] == pytest.approx(9.0)

    def test_merge_tie_prefers_month_end_row(self, tmp_db):
        _put(tmp_db, "AAPL", "annual", "2025-09-27", "2026-10-14T09:58:06", 1.0)
        _put(tmp_db, "AAPL", "annual", "2025-09-30", "2026-10-14T09:58:06", 2.0)
        tmp_db.commit()
        fund_mod.merge_period_duplicates(tmp_db)
        rows = tmp_db.execute(sa.select(fundamentals_table.c.total_debt)).scalars().all()
        assert rows == [2.0]


class TestGrowthRatiosByDate:
    def test_income_gap_uses_dated_periods(self, tmp_db):
        # WMT-like: income statement skips 2025-04-30
        dates = ["2026-01-31", "2025-10-31", "2025-07-31", "2025-01-31"]
        inc = _stmt({"Total Revenue": [130.0, 120.0, 110.0, 100.0]}, dates)
        empty = pd.DataFrame()

        class FakeTicker:
            quarterly_income_stmt = inc
            quarterly_balance_sheet = quarterly_cashflow = empty
            income_stmt = balance_sheet = cashflow = empty

        with patch("data.fundamentals.yf.Ticker", return_value=FakeTicker()):
            fund_mod._process_ticker_yfinance(tmp_db, "WMT")
        rows = {
            r.period_end: (r.revenue_growth_qoq, r.revenue_growth_yoy)
            for r in tmp_db.execute(sa.select(fundamentals_table))
        }
        assert rows["2026-01-31"] == (pytest.approx(130 / 120 - 1), pytest.approx(130 / 100 - 1))
        assert rows["2025-10-31"][0] == pytest.approx(120 / 110 - 1)
        assert rows["2025-07-31"] == (None, None)  # 2025-04-30 missing: no QoQ, no wrong period


class TestGrowthRatiosAnnualAndLeap:
    def test_annual_rows_and_february_year_end(self, tmp_db):
        dates = ["2025-02-28", "2024-02-29", "2023-02-28"]
        inc = _stmt(
            {"Total Revenue": [130.0, 120.0, 100.0], "Net Income": [13.0, 12.0, 10.0]}, dates
        )
        empty = pd.DataFrame()

        class FakeTicker:
            quarterly_income_stmt = quarterly_balance_sheet = quarterly_cashflow = empty
            income_stmt, balance_sheet, cashflow = inc, empty, empty

        with patch("data.fundamentals.yf.Ticker", return_value=FakeTicker()):
            fund_mod._process_ticker_yfinance(tmp_db, "X")
        rows = {
            r.period_end: (r.revenue_growth_qoq, r.revenue_growth_yoy, r.earnings_growth_yoy)
            for r in tmp_db.execute(sa.select(fundamentals_table))
        }
        assert rows["2025-02-28"] == (
            pytest.approx(130 / 120 - 1),
            pytest.approx(130 / 120 - 1),
            pytest.approx(13 / 12 - 1),
        )
        assert rows["2024-02-29"][1] == pytest.approx(120 / 100 - 1)
        assert rows["2023-02-28"] == (None, None, None)
