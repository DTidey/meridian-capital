"""Market cap snapshots and the effective-shares helper (packet 42 AC1, AC2)."""

import pandas as pd
import pytest
import sqlalchemy as sa

import data.yahoo_info as yahoo_info
from data._utils import utc_now
from data.db import daily_prices, market_caps
from data.market_caps import update_market_caps
from data.short_interest import update_short_interest
from data.yahoo_info import YahooInfo
from factors.loader import load_scoring_data
from factors.market_cap import market_caps as compute_market_caps
from factors.market_cap import shares_from_snapshots
from tests.yahoo_fake import INFO, FakeYahoo

BRK_INFO = {**INFO, "marketCap": 1_153_821_179_904}


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(yahoo_info.time, "sleep", lambda s: None)

    def install(f):
        monkeypatch.setattr(yahoo_info.yf, "Ticker", f)
        return f

    return install


def _stored(conn):
    rows = conn.execute(sa.select(market_caps.c.ticker, market_caps.c.market_cap)).fetchall()
    return dict(rows)


# ---------------------------------------------------------------------------
# AC1: ingestion
# ---------------------------------------------------------------------------


class TestUpdateMarketCaps:
    def test_stores_market_cap(self, tmp_db, fake):
        fake(FakeYahoo(default_info=BRK_INFO))
        assert update_market_caps(tmp_db, ["BRK-B"]) == {"BRK-B": True}
        assert _stored(tmp_db) == {"BRK-B": pytest.approx(1_153_821_179_904)}
        date = tmp_db.execute(sa.select(market_caps.c.date)).scalar()
        assert date == utc_now().strftime("%Y-%m-%d")

    def test_skips_tickers_with_todays_row(self, tmp_db, fake):
        f = fake(FakeYahoo(default_info=BRK_INFO))
        update_market_caps(tmp_db, ["BRK-B"])
        assert update_market_caps(tmp_db, ["BRK-B"], info=YahooInfo()) == {}
        assert f.calls["info"]["BRK-B"] == 1

    def test_shared_info_makes_no_extra_request(self, tmp_db, fake):
        f = fake(FakeYahoo(default_info=BRK_INFO))
        info = YahooInfo()
        update_short_interest(tmp_db, ["BRK-B", "CME"], info=info)
        update_market_caps(tmp_db, ["BRK-B", "CME"], info=info)
        assert f.calls["info"] == {"BRK-B": 1, "CME": 1}

    @pytest.mark.parametrize("value", [None, 0, -5.0, float("nan"), "n/a"])
    def test_bad_value_not_stored(self, tmp_db, fake, value):
        fake(FakeYahoo(default_info={**INFO, "marketCap": value}))
        assert update_market_caps(tmp_db, ["X"]) == {"X": False}
        assert _stored(tmp_db) == {}

    def test_failed_fetch_not_stored(self, tmp_db, fake):
        fake(FakeYahoo(info={"X": [RuntimeError("429")]}))
        assert update_market_caps(tmp_db, ["X"]) == {"X": False}
        assert _stored(tmp_db) == {}


# ---------------------------------------------------------------------------
# AC2: effective shares
# ---------------------------------------------------------------------------


class TestSharesFromSnapshots:
    def test_latest_used_within_guard(self):
        assert shares_from_snapshots([(1300.0, 10.0), (1000.0, 10.0)]) == pytest.approx(130.0)

    def test_glitch_keeps_previous(self):
        # CME-style 1000x glitch in the latest snapshot
        assert shares_from_snapshots([(1.0, 10.0), (1000.0, 10.0)]) == pytest.approx(100.0)
        assert shares_from_snapshots([(2000.0, 10.0), (1000.0, 10.0)]) == pytest.approx(100.0)

    def test_single_snapshot_used(self):
        assert shares_from_snapshots([(1000.0, 10.0)]) == pytest.approx(100.0)

    def test_none(self):
        assert shares_from_snapshots([]) is None
        assert shares_from_snapshots([(1000.0, None)]) is None

    # Adversarial: an unusable newest snapshot must not disable the guard for the next pair
    def test_unusable_snapshot_skipped(self):
        snaps = [(None, 10.0), (1.0, 10.0), (1000.0, 10.0)]
        assert shares_from_snapshots(snaps) == pytest.approx(100.0)

    # Adversarial: exactly 1.5x is accepted, just over is not
    def test_guard_boundary(self):
        assert shares_from_snapshots([(1500.0, 10.0), (1000.0, 10.0)]) == pytest.approx(150.0)
        assert shares_from_snapshots([(1501.0, 10.0), (1000.0, 10.0)]) == pytest.approx(100.0)


def _prices(ticker, closes):
    return pd.DataFrame(
        [{"ticker": ticker, "date": pd.Timestamp(d), "close": c, "adj_close": c} for d, c in closes]
    )


def _snaps(rows):
    df = pd.DataFrame(rows, columns=["ticker", "date", "market_cap"])
    df["date"] = pd.to_datetime(df["date"])
    return df


def _funds(ticker, shares, total_assets=1e9):
    return pd.DataFrame(
        [
            {
                "ticker": ticker,
                "period_end": pd.Timestamp("2026-06-30"),
                "shares_outstanding": shares,
                "total_assets": total_assets,
            }
        ]
    )


class TestMarketCaps:
    def test_brk_uses_snapshot_not_balance_sheet(self):
        prices = _prices("BRK-B", [("2026-10-21", 538.99)])
        snaps = _snaps([("BRK-B", "2026-10-21", 1_153_821_179_904)])
        result = compute_market_caps(prices, snaps, _funds("BRK-B", 1_431_693))
        assert result["BRK-B"] == pytest.approx(1_153_821_179_904)

    def test_latest_close_scales_market_cap(self):
        prices = _prices("A", [("2026-10-20", 10.0), ("2026-10-21", 11.0)])
        snaps = _snaps([("A", "2026-10-20", 1000.0)])
        assert compute_market_caps(prices, snaps, _funds("A", 1))["A"] == pytest.approx(1100.0)

    def test_fallback_to_balance_sheet(self, caplog):
        prices = _prices("A", [("2026-10-21", 10.0)])
        with caplog.at_level("INFO"):
            result = compute_market_caps(prices, None, _funds("A", 50.0))
        assert result["A"] == pytest.approx(500.0)
        assert "1 tickers fell back to balance-sheet shares" in caplog.text

    def test_fallback_skips_quarter_without_balance_sheet(self):
        funds = pd.concat(
            [
                _funds("A", 50.0),
                _funds("A", None, total_assets=None).assign(period_end=pd.Timestamp("2026-09-30")),
            ]
        )
        result = compute_market_caps(_prices("A", [("2026-10-21", 10.0)]), None, funds)
        assert result["A"] == pytest.approx(500.0)

    def test_no_shares_is_nan(self):
        result = compute_market_caps(_prices("A", [("2026-10-21", 10.0)]), None, _funds("A", None))
        assert pd.isna(result["A"])

    def test_snapshot_on_non_trading_day_uses_earlier_close(self):
        prices = _prices("A", [("2026-10-16", 10.0), ("2026-10-19", 20.0)])
        snaps = _snaps([("A", "2026-10-18", 1000.0)])  # a Sunday: Friday's close of 10
        assert compute_market_caps(prices, snaps, _funds("A", 1))["A"] == pytest.approx(2000.0)

    def test_split_between_snapshots_not_a_glitch(self):
        # 2:1 split on 10-21; our closes are split-adjusted retroactively (packet 38)
        prices = _prices("APH", [("2026-10-20", 79.0), ("2026-10-21", 80.0)])
        snaps = _snaps([("APH", "2026-10-20", 195e9), ("APH", "2026-10-21", 197e9)])
        result = compute_market_caps(prices, snaps, _funds("APH", 1))
        assert result["APH"] == pytest.approx(197e9)

    def test_glitch_snapshot_ignored(self):
        prices = _prices("CME", [("2026-10-20", 295.0), ("2026-10-21", 296.0)])
        snaps = _snaps([("CME", "2026-10-20", 106e9), ("CME", "2026-10-21", 106e6)])
        result = compute_market_caps(prices, snaps, _funds("CME", 359_275))
        assert result["CME"] == pytest.approx(106e9 / 295.0 * 296.0)


class TestLoader:
    def test_market_caps_loaded_within_window(self, tmp_db):
        for d, v in (("2026-09-01", 1.0), ("2026-10-20", 2.0), ("2026-10-21", 3.0)):
            tmp_db.execute(market_caps.insert().values(ticker="A", date=d, market_cap=v))
        tmp_db.execute(
            daily_prices.insert().values(ticker="A", date="2026-10-20", close=1.0, adj_close=1.0)
        )
        tmp_db.commit()
        data = load_scoring_data(tmp_db, {}, "2026-10-20")
        loaded = data["market_caps"]
        assert loaded["market_cap"].tolist() == [2.0]
        assert loaded["date"].tolist() == [pd.Timestamp("2026-10-20")]
