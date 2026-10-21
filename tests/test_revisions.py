"""Tests for factors/revisions.py: percent revisions from Yahoo's +1y EPS trend (packet 41)."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from factors._utils import guarded_current
from factors.revisions import COLS, _raw_revisions, compute

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TREND_COLS = ["ticker", "date", "current", "days_7", "days_30", "days_60", "days_90"]
_SNAP = "2026-10-21"


def _row(ticker, current, d7=None, d30=None, d60=None, d90=None, date=_SNAP):
    vals = [current, d7, d30, d60, d90]
    return dict(zip(_TREND_COLS, [ticker, pd.Timestamp(date), *vals], strict=True))


def _flat(ticker, eps, date=_SNAP):
    return _row(ticker, eps, eps, eps, eps, eps, date)


def _trends(rows):
    return pd.DataFrame(rows, columns=_TREND_COLS).astype(dict.fromkeys(_TREND_COLS[2:], float))


def _make_data(tickers, rows=None):
    universe = pd.DataFrame([(t, "IT") for t in tickers], columns=["ticker", "sector"])
    rows = [_flat(t, 5.0) for t in tickers] if rows is None else rows
    return {"universe": universe, "estimate_trends": _trends(rows)}


def _raw(rows):
    return _raw_revisions(_trends(rows).set_index("ticker"))


_CONFIG = {"scoring": {"min_sector_size": 2}}


# ---------------------------------------------------------------------------
# AC4: percent change of the +1y consensus against its 30/60/90-days-ago value
# ---------------------------------------------------------------------------


class TestTrendRevisions:
    def test_percent_change_formula(self):
        raw = _raw([_row("A", 11.0, 11.0, 10.0, 8.0, 12.5)])
        assert raw.loc["A", "rev_30d"] == pytest.approx(0.10)
        assert raw.loc["A", "rev_60d"] == pytest.approx(0.375)
        assert raw.loc["A", "rev_90d"] == pytest.approx(-0.12)

    def test_percent_not_dollars(self):
        # BIG: +$10 (+10%); SMALL: +$0.50 (+50%). Dollar revisions ranked BIG first (audit M2).
        rows = [_row("BIG", 110.0, 110.0, 100.0), _row("SMALL", 1.5, 1.5, 1.0)]
        result = compute(_make_data(["BIG", "SMALL"], rows), _CONFIG)
        assert result.loc["SMALL", "rev_30d"] > result.loc["BIG", "rev_30d"]

    def test_denominator_floor(self):
        raw = _raw([_row("DOC", 0.14, 0.14, 0.02, -0.05)])
        assert raw.loc["DOC", "rev_30d"] == pytest.approx(0.12 / 0.10)
        assert raw.loc["DOC", "rev_60d"] == pytest.approx(0.19 / 0.10)

    def test_negative_estimate_uses_absolute_denominator(self):
        raw = _raw([_row("NEG", -0.50, -0.50, -1.00)])
        assert raw.loc["NEG", "rev_30d"] == pytest.approx(0.5)

    def test_missing_window_only_blanks_that_sub_factor(self):
        raw = _raw([_row("A", 11.0, 11.0, 10.0, None, 10.0)])
        assert raw.loc["A", "rev_30d"] == pytest.approx(0.10)
        assert np.isnan(raw.loc["A", "rev_60d"])
        assert raw.loc["A", "rev_90d"] == pytest.approx(0.10)

    def test_no_snapshot_scores_neutral(self):
        rows = [_row("UP", 11.0, 11.0, 10.0, 10.0, 10.0), _row("DN", 9.0, 9.0, 10.0, 10.0, 10.0)]
        result = compute(_make_data(["UP", "DN", "NONE"], rows), _CONFIG)
        for col in COLS:
            assert result.loc["NONE", col] == pytest.approx(50.0)
        assert result.loc["UP", "rev_30d"] > result.loc["DN", "rev_30d"]

    def test_empty_trends_all_neutral(self):
        result = compute(_make_data(["AAPL", "MSFT"], rows=[]), _CONFIG)
        assert (result == 50.0).all().all()

    def test_latest_snapshot_used(self):
        rows = [
            _row("A", 20.0, 20.0, 10.0, date="2026-10-20"),
            _row("A", 11.0, 11.0, 10.0, date="2026-10-21"),
        ]
        assert _raw_from_compute_input(rows).loc["A", "rev_30d"] == pytest.approx(0.10)

    # Adversarial: columns come from one snapshot; an older day's days_90 never fills a gap.
    def test_latest_snapshot_not_mixed_with_older(self):
        rows = [
            _row("A", 11.0, 11.0, 10.0, 10.0, 10.0, date="2026-10-20"),
            _row("A", 11.0, 11.0, 10.0, 10.0, None, date="2026-10-21"),
        ]
        assert np.isnan(_raw_from_compute_input(rows).loc["A", "rev_90d"])

    def test_stale_snapshot_not_loaded(self, tmp_db):
        _insert_trend(tmp_db, "OLD", "2026-10-15", 11.0, 10.0)  # 6 days before the score date
        _insert_trend(tmp_db, "EDGE", "2026-10-16", 11.0, 10.0)  # exactly 5 days: fresh
        _insert_trend(tmp_db, "LOW", "2026-10-21", 9.0, 10.0)
        result = _score_from_loader(tmp_db, ["OLD", "EDGE", "LOW"], "2026-10-21")
        assert result.loc["OLD", "rev_30d"] == pytest.approx(50.0)
        assert result.loc["EDGE", "rev_30d"] > result.loc["LOW", "rev_30d"]

    # Adversarial: the 0y period row never feeds the +1y revisions.
    def test_only_plus_1y_period_used(self, tmp_db):
        _insert_trend(tmp_db, "A", "2026-10-21", 11.0, 10.0, period="0y")
        _insert_trend(tmp_db, "B", "2026-10-21", 9.0, 10.0)
        result = _score_from_loader(tmp_db, ["A", "B"], "2026-10-21")
        assert result.loc["A", "rev_30d"] == pytest.approx(50.0)


def _raw_from_compute_input(rows):
    from factors._utils import latest_trends

    return _raw_revisions(latest_trends(_trends(rows)))


def _insert_trend(conn, ticker, date, current, d30, period="+1y"):
    from data.db import estimate_trends

    conn.execute(
        estimate_trends.insert().values(
            ticker=ticker, date=date, period=period, current=current, days_7=current, days_30=d30
        )
    )
    conn.commit()


def _score_from_loader(conn, tickers, score_date):
    from datetime import date

    from factors.loader import _load_estimate_trends

    trends = _load_estimate_trends(conn, date.fromisoformat(score_date))
    universe = pd.DataFrame([(t, "IT") for t in tickers], columns=["ticker", "sector"])
    return compute({"universe": universe, "estimate_trends": trends}, _CONFIG)


# ---------------------------------------------------------------------------
# AC5: glitch guard against Yahoo's own 7-days-ago value
# ---------------------------------------------------------------------------


class TestGlitchGuard:
    def test_pgr_glitch_uses_days_7(self):
        raw = _raw([_row("PGR", 1012.0, 16.36848, 16.27934, 16.0, 15.8)])
        assert raw.loc["PGR", "rev_30d"] == pytest.approx((16.36848 - 16.27934) / 16.27934)
        assert raw.loc["PGR", "rev_90d"] == pytest.approx((16.36848 - 15.8) / 15.8)

    def test_large_move_replaced(self):
        assert guarded_current({"current": 16.0, "days_7": 10.0}) == pytest.approx(10.0)
        assert guarded_current({"current": 4.0, "days_7": 10.0}) == pytest.approx(10.0)

    def test_normal_move_kept(self):
        assert guarded_current({"current": 12.0, "days_7": 10.0}) == pytest.approx(12.0)

    def test_days_7_missing_returns_current(self):
        assert guarded_current({"current": 1012.0, "days_7": np.nan}) == pytest.approx(1012.0)
        assert guarded_current({"current": 1012.0, "days_7": None}) == pytest.approx(1012.0)

    # Adversarial: exactly 50% is not "more than 50%".
    def test_exactly_half_kept(self):
        assert guarded_current({"current": 15.0, "days_7": 10.0}) == pytest.approx(15.0)

    # Adversarial: the $0.10 floor applies to the guard, so a near-zero estimate can move.
    def test_floor_near_zero(self):
        assert guarded_current({"current": 0.09, "days_7": 0.05}) == pytest.approx(0.09)
        assert guarded_current({"current": 0.11, "days_7": 0.05}) == pytest.approx(0.05)

    def test_no_row_is_nan(self):
        assert np.isnan(guarded_current(None))

    def test_guard_count_logged(self, caplog):
        rows = [_row("PGR", 1012.0, 16.37, 16.28), _row("CB", 20.0, 20.0, 19.0)]
        with caplog.at_level("INFO"):
            compute(_make_data(["PGR", "CB"], rows), _CONFIG)
        assert (
            "Revisions: glitch guard used the 7-day-ago consensus for 1 ticker (PGR)" in caplog.text
        )


# ---------------------------------------------------------------------------
# AC6: a fiscal-year roll-over is not a revision
# ---------------------------------------------------------------------------


class TestRollOver:
    def test_forward_eps_step_ignored_trend_flat(self):
        data = _make_data(
            ["MU", "UP", "DN"],
            [
                _flat("MU", 205.3),
                _row("UP", 11.0, 11.0, 10.0, 10.0, 10.0),
                _row("DN", 9.0, 9.0, 10.0, 10.0, 10.0),
            ],
        )
        # forwardEps snapshots stepping 161.5 -> 205.3 as MU reports Q4; not read any more.
        data["estimates"] = pd.DataFrame(
            {
                "ticker": "MU",
                "date": pd.to_datetime(["2026-07-21", "2026-08-21", "2026-09-21", "2026-10-21"]),
                "eps_estimate_fwd": [161.5, 161.5, 161.5, 205.3],
            }
        )
        result = compute(data, _CONFIG)
        assert _raw(data["estimate_trends"].to_dict("records")).loc["MU"].tolist() == [0, 0, 0]
        for col in ("rev_30d", "rev_60d"):
            assert result.loc["MU", col] == pytest.approx(200 / 3)
            assert result.loc["UP", col] == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


class TestStructure:
    def test_columns_present(self):
        result = compute(_make_data(["AAPL", "MSFT"]), _CONFIG)
        assert set(COLS + ["revisions_score"]).issubset(result.columns)

    def test_scores_0_to_100(self):
        rows = [_row("A", 11.0, 11.0, 10.0), _row("B", 9.0, 9.0, 10.0), _flat("C", 5.0)]
        result = compute(_make_data(["A", "B", "C"], rows), _CONFIG)
        for col in COLS + ["revisions_score"]:
            assert result[col].between(0, 100).all(), f"{col} out of range"

    def test_empty_universe(self):
        assert compute(_make_data([]), _CONFIG).empty
