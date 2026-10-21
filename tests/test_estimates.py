"""Analyst estimates step: estimate_trends rows from the shared earningsTrend (packet 41 AC1)."""

import pytest
import sqlalchemy as sa

import data.yahoo_info as yahoo_info
from data.db import analyst_estimates, estimate_trends
from data.estimates import update_estimates
from data.yahoo_info import YahooInfo, YahooTrend
from tests.yahoo_fake import INFO, PGR_TREND, FakeYahoo, trend

_BOOM = RuntimeError("Too Many Requests")


@pytest.fixture
def sleeps(monkeypatch):
    recorded = []
    monkeypatch.setattr(yahoo_info.time, "sleep", recorded.append)
    return recorded


@pytest.fixture
def fake(monkeypatch):
    def install(**kwargs):
        f = FakeYahoo(**kwargs)
        monkeypatch.setattr("yfinance.Ticker", f)
        return f

    return install


def _trend_rows(conn, ticker=None):
    query = sa.select(
        estimate_trends.c.ticker,
        estimate_trends.c.period,
        estimate_trends.c.current,
        estimate_trends.c.days_7,
        estimate_trends.c.days_30,
        estimate_trends.c.days_60,
        estimate_trends.c.days_90,
    ).order_by(estimate_trends.c.ticker, estimate_trends.c.period)
    if ticker:
        query = query.where(estimate_trends.c.ticker == ticker)
    return [tuple(r) for r in conn.execute(query)]


class TestTrendRows:
    def test_stores_0y_and_plus_1y(self, tmp_db, fake, sleeps):
        f = fake(default_info=INFO, trend={"PGR": [PGR_TREND]})
        assert update_estimates(tmp_db, ["PGR"]) == {"PGR": True}
        rows = _trend_rows(tmp_db)
        assert rows == [
            ("PGR", "+1y", 16.30119, 16.36848, 16.27934, 16.0, 15.8),
            ("PGR", "0y", 15.1, 15.12, 15.0, 14.8, 14.5),
        ]
        assert f.calls["trend"]["PGR"] == 1

    def test_row_has_date_and_fetched_at(self, tmp_db, fake, sleeps):
        from data._utils import utc_now

        fake(default_info=INFO, default_trend=PGR_TREND)
        update_estimates(tmp_db, ["PGR"])
        row = tmp_db.execute(sa.select(estimate_trends).limit(1)).mappings().one()
        assert row["date"] == utc_now().strftime("%Y-%m-%d")
        assert row["fetched_at"].startswith(row["date"])

    def test_period_without_current_skipped(self, tmp_db, fake, sleeps):
        no_current = {"0y": trend(3.0, d7=3.0), "+1y": {"30daysAgo": 3.5}}
        fake(default_info=INFO, default_trend=no_current)
        update_estimates(tmp_db, ["X"])
        assert [r[1] for r in _trend_rows(tmp_db)] == ["0y"]

    def test_no_coverage_no_rows(self, tmp_db, fake, sleeps, caplog):
        fake(default_info=INFO, default_trend={})
        with caplog.at_level("INFO"):
            assert update_estimates(tmp_db, ["NEW"]) == {"NEW": True}
        assert _trend_rows(tmp_db) == []
        assert "; trends 1/1" in caplog.text

    def test_missing_window_stored_as_null(self, tmp_db, fake, sleeps):
        fake(default_info=INFO, default_trend={"+1y": trend(2.0, d7=2.0, d30=1.9)})
        update_estimates(tmp_db, ["X"])
        assert _trend_rows(tmp_db) == [("X", "+1y", 2.0, 2.0, 1.9, None, None)]

    def test_coverage_line(self, tmp_db, fake, sleeps, caplog):
        fake(default_info=INFO, trend={"BAD": [_BOOM]}, default_trend=PGR_TREND)
        info = YahooInfo()
        info.get("PGR")
        with caplog.at_level("INFO"):
            update_estimates(tmp_db, ["PGR", "BAD"], info=info, trend=YahooTrend())
        assert (
            "Analyst estimates: 2/2 tickers fetched (1 from the shared .info); trends 1/2"
            in caplog.text
        )
        errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
        assert errors == ["Analyst estimate trends: 1/2 tickers fetched after retries (below 95%)"]

    def test_trend_second_pass(self, tmp_db, fake, sleeps):
        f = fake(default_info=INFO, trend={"PGR": [_BOOM, _BOOM, _BOOM, PGR_TREND]})
        update_estimates(tmp_db, ["PGR"])
        assert f.calls["trend"]["PGR"] == 4
        assert len(_trend_rows(tmp_db)) == 2
        assert sleeps == [5, 15, 60]

    def test_shared_trend_not_refetched(self, tmp_db, fake, sleeps):
        f = fake(default_info=INFO, default_trend=PGR_TREND)
        tr = YahooTrend()
        tr.get("PGR")
        update_estimates(tmp_db, ["PGR"], trend=tr)
        assert f.calls["trend"]["PGR"] == 1
        assert len(_trend_rows(tmp_db)) == 2

    # Adversarial: a failed .info does not stop the trend snapshot.
    def test_info_failure_still_stores_trend(self, tmp_db, fake, sleeps):
        fake(info={"PGR": [_BOOM]}, default_trend=PGR_TREND)
        assert update_estimates(tmp_db, ["PGR"]) == {"PGR": False}
        assert len(_trend_rows(tmp_db)) == 2

    # Adversarial (spec edge case): a second run the same day overwrites the trend snapshot,
    # although analyst_estimates already has today's row and is skipped.
    def test_same_day_rerun_upserts_trend(self, tmp_db, fake, sleeps):
        fake(default_info=INFO, trend={"PGR": [PGR_TREND, {"+1y": trend(17.0, d7=16.4)}]})
        update_estimates(tmp_db, ["PGR"])
        update_estimates(tmp_db, ["PGR"])
        rows = _trend_rows(tmp_db)
        assert ("PGR", "+1y", 17.0, 16.4, None, None, None) in rows
        assert len(rows) == 2
        count = tmp_db.execute(sa.select(sa.func.count()).select_from(analyst_estimates)).scalar()
        assert count == 1
