"""Shared TTM helper and the dashboard's use of the market cap snapshots (packet 42 AC5, AC10)."""

import pytest

from dashboard.ticker_valuation import _snapshot_shares, valuation
from data.db import daily_prices, market_caps
from factors.ttm import ttm

_ENDS = ("2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30")


def _q(end, **values):
    return {"period_end": end, "cfo": 10.0, **values}


class TestZeroWhenOmitted:
    def test_omitted_dividend_with_cash_flow_statement_is_zero(self):
        rows = [_q(e) for e in _ENDS]
        assert ttm(rows, "dividends_paid") == 0.0
        assert ttm(rows, "buybacks") == 0.0

    def test_without_cash_flow_statement_missing(self):
        assert ttm([_q(e, cfo=None) for e in _ENDS], "dividends_paid") is None

    def test_only_cash_return_lines(self):
        # Adversarial: an omitted revenue line is missing data, never zero
        assert ttm([_q(e) for e in _ENDS], "revenue") is None

    def test_buybacks_sum_sizes(self):
        rows = [_q(e, buybacks=v) for e, v in zip(_ENDS, (-5.0, -5.0, 2.0, -5.0), strict=True)]
        assert ttm(rows, "buybacks") == pytest.approx(17.0)

    def test_stopped_dividend_counts_zero_not_last_year(self):
        recent = [_q(e) for e in _ENDS]
        older = [_q(e, dividends_paid=-4.0) for e in ("2025-06-30", "2025-03-31")]
        assert ttm(recent + older, "dividends_paid") == 0.0


class TestDashboardShares:
    def test_snapshot_shares_override_balance_sheet(self):
        rows = [_q(e, shares_outstanding=1.43e6, total_debt=0.0, cash=0.0) for e in _ENDS]
        v = valuation(538.99, rows, shares=2.14e9)
        assert v["market_cap"] == pytest.approx(538.99 * 2.14e9)

    def test_without_snapshot_balance_sheet_used(self):
        rows = [_q(e, shares_outstanding=15e9) for e in _ENDS]
        assert valuation(100.0, rows)["market_cap"] == pytest.approx(1.5e12)

    def test_snapshot_shares_from_db_with_guard(self, tmp_db):
        for d, close in (("2026-10-19", 290.0), ("2026-10-20", 295.0), ("2026-10-21", 296.0)):
            tmp_db.execute(
                daily_prices.insert().values(ticker="CME", date=d, close=close, adj_close=close)
            )
        for d, mcap in (("2026-10-20", 106e9), ("2026-10-21", 106e6)):  # glitch on 10-21
            tmp_db.execute(market_caps.insert().values(ticker="CME", date=d, market_cap=mcap))
        tmp_db.commit()
        assert _snapshot_shares(tmp_db, "CME", "2026-10-21") == pytest.approx(106e9 / 295.0)

    def test_no_snapshots(self, tmp_db):
        assert _snapshot_shares(tmp_db, "CME", "2026-10-21") is None
        assert _snapshot_shares(tmp_db, "CME", None) is None

    # Adversarial: the dashboard ignores snapshots scoring would ignore (older than 30 days)
    def test_stale_snapshot_ignored(self, tmp_db):
        tmp_db.execute(
            daily_prices.insert().values(ticker="A", date="2026-08-03", close=10.0, adj_close=10.0)
        )
        tmp_db.execute(market_caps.insert().values(ticker="A", date="2026-08-03", market_cap=1e9))
        tmp_db.commit()
        assert _snapshot_shares(tmp_db, "A", "2026-10-21") is None
        assert _snapshot_shares(tmp_db, "A", "2026-08-20") == pytest.approx(1e8)
