"""Tests for risk/factor_monitor.py (packet 46)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from datetime import date, timedelta
from unittest.mock import patch

import pandas as pd
import pytest
import sqlalchemy as sa

import analysis.db  # noqa: F401
import factors.db  # noqa: F401
import portfolio.db  # noqa: F401
import risk.db  # noqa: F401
from data.db import initialise_schema
from factors.db import crowding_flags, factor_scores
from portfolio.db import portfolio_history
from risk.db import risk_log
from risk.factor_monitor import _load_history_spreads, run_factor_monitor

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TODAY = "2026-10-22"
_FACTORS = [
    "momentum_score",
    "quality_score",
    "value_score",
    "revisions_score",
    "insider_score",
    "growth_score",
    "short_interest_score",
    "institutional_score",
]


@pytest.fixture
def mem_db():
    engine = sa.create_engine("sqlite:///:memory:", future=True)
    initialise_schema(engine)
    conn = engine.connect()
    yield conn
    conn.close()
    engine.dispose()


def _config(threshold=1.5):
    return {"risk": {"factor_monitor": {"alert_z_threshold": threshold}}}


def _past_dates(n):
    """n distinct ISO dates strictly before _TODAY, oldest first."""
    end = date.fromisoformat(_TODAY)
    return [(end - timedelta(days=n - i)).isoformat() for i in range(n)]


def _insert_scores(conn, score_date, scores):
    """scores: {ticker: {factor: value}}; unlisted factors are 50.0."""
    rows = []
    for ticker, overrides in scores.items():
        row = dict.fromkeys(_FACTORS, 50.0)
        row.update(overrides)
        rows.append({"ticker": ticker, "score_date": score_date, **row})
    conn.execute(factor_scores.insert(), rows)
    conn.commit()


def _insert_snapshot(conn, snapshot_date, book, recorded_at="2026-10-01T22:00:00"):
    """book: list of (ticker, direction, weight)."""
    rows = [
        {
            "snapshot_date": snapshot_date,
            "ticker": ticker,
            "direction": direction,
            "weight": weight,
            "recorded_at": recorded_at,
        }
        for ticker, direction, weight in book
    ]
    conn.execute(portfolio_history.insert(), rows)
    conn.commit()


_BOOK = [("L1", "LONG", 0.05), ("S1", "SHORT", -0.05)]


def _momentum_day(conn, score_date, spread):
    """Scores where the L1-minus-S1 momentum spread equals `spread`; other factors 50."""
    _insert_scores(
        conn,
        score_date,
        {"L1": {"momentum_score": 50 + spread / 2}, "S1": {"momentum_score": 50 - spread / 2}},
    )


def _history(conn, spreads):
    """One snapshot per past date with the given momentum spreads."""
    for d, s in zip(_past_dates(len(spreads)), spreads, strict=True):
        _momentum_day(conn, d, s)
        _insert_snapshot(conn, d, _BOOK)


def _positions(book=_BOOK):
    return pd.DataFrame(book, columns=["ticker", "direction", "weight"])


def _risk_rows(conn):
    return conn.execute(sa.select(risk_log.c.result, risk_log.c.reason)).fetchall()


# ---------------------------------------------------------------------------
# AC1 — no book
# ---------------------------------------------------------------------------


class TestNoPositions:
    @pytest.mark.parametrize(
        "book",
        [[], [("L1", "LONG", 0.05)], [("S1", "SHORT", -0.05)]],
        ids=["empty", "long-only", "short-only"],
    )
    def test_returns_no_alerts_and_logs_ok(self, mem_db, book):
        _momentum_day(mem_db, _TODAY, 40)
        alerts = run_factor_monitor(mem_db, _positions(book), _TODAY, _config())
        assert alerts == []
        assert _risk_rows(mem_db) == [("OK", "no positions")]

    def test_does_not_load_history(self, mem_db):
        with patch("risk.factor_monitor._load_history_spreads") as loader:
            run_factor_monitor(mem_db, _positions([]), _TODAY, _config())
        loader.assert_not_called()

    def test_whatif_writes_nothing(self, mem_db):
        assert run_factor_monitor(mem_db, _positions([]), _TODAY, _config(), whatif=True) == []
        assert _risk_rows(mem_db) == []

    def test_empty_frame_without_columns(self, mem_db):
        # load_positions on an empty table still has columns, but a bare frame must not crash;
        # today's scores exist so the no-positions guard, not the no-scores one, is exercised
        _momentum_day(mem_db, _TODAY, 40)
        assert run_factor_monitor(mem_db, pd.DataFrame(), _TODAY, _config()) == []
        assert _risk_rows(mem_db) == [("OK", "no positions")]


# ---------------------------------------------------------------------------
# AC2 — baseline from portfolio_history
# ---------------------------------------------------------------------------


class TestBaseline:
    def test_spreads_use_same_date_scores(self, mem_db):
        _history(mem_db, [10, 12, 14])
        hist = _load_history_spreads(mem_db, _TODAY)
        assert sorted(hist["momentum_score"]) == pytest.approx([10, 12, 14])
        assert hist["value_score"] == pytest.approx([0, 0, 0])

    def test_date_without_scores_is_skipped(self, mem_db):
        _history(mem_db, [10, 12])
        _insert_snapshot(mem_db, "2026-08-01", _BOOK)
        assert len(_load_history_spreads(mem_db, _TODAY)["momentum_score"]) == 2

    def test_one_sided_snapshot_is_skipped(self, mem_db):
        _history(mem_db, [10, 12])
        _momentum_day(mem_db, "2026-08-01", 30)
        _insert_snapshot(mem_db, "2026-08-01", [("L1", "LONG", 0.05)])
        assert sorted(_load_history_spreads(mem_db, _TODAY)["momentum_score"]) == [10, 12]

    def test_two_commits_use_latest_only(self, mem_db):
        d = _past_dates(1)[0]
        _momentum_day(mem_db, d, 20)
        _insert_snapshot(mem_db, d, [("L1", "SHORT", -0.05)], recorded_at="2026-10-21T10:00:00")
        _insert_snapshot(mem_db, d, _BOOK, recorded_at="2026-10-21T22:00:00")
        assert _load_history_spreads(mem_db, _TODAY)["momentum_score"] == pytest.approx([20])

    def test_ticker_closed_between_commits_does_not_leak(self, mem_db):
        d = _past_dates(1)[0]
        _insert_scores(
            mem_db,
            d,
            {
                "L1": {"momentum_score": 60},
                "L2": {"momentum_score": 100},
                "S1": {"momentum_score": 40},
            },
        )
        _insert_snapshot(mem_db, d, [*_BOOK, ("L2", "LONG", 0.05)], recorded_at="2026-10-21T10:00")
        _insert_snapshot(mem_db, d, _BOOK, recorded_at="2026-10-21T22:00")
        assert _load_history_spreads(mem_db, _TODAY)["momentum_score"] == pytest.approx([20])

    def test_today_snapshot_is_excluded(self, mem_db):
        _history(mem_db, [10, 12])
        _momentum_day(mem_db, _TODAY, 90)
        _insert_snapshot(mem_db, _TODAY, _BOOK)
        assert sorted(_load_history_spreads(mem_db, _TODAY)["momentum_score"]) == [10, 12]

    def test_capped_at_252_most_recent_dates(self, mem_db):
        # oldest 8 dates carry spread 99; only the newest 252 (spread 10) are used
        _history(mem_db, [99] * 8 + [10] * 252)
        hist = _load_history_spreads(mem_db, _TODAY)["momentum_score"]
        assert len(hist) == 252
        assert set(hist) == {10}

    def test_unscored_ticker_counts_as_neutral(self, mem_db):
        d = _past_dates(1)[0]
        _insert_scores(mem_db, d, {"L1": {"momentum_score": 70}})
        _insert_snapshot(mem_db, d, _BOOK)
        assert _load_history_spreads(mem_db, _TODAY)["momentum_score"] == pytest.approx([20])

    def test_null_weight_row_is_ignored(self, mem_db):
        d = _past_dates(1)[0]
        _insert_scores(
            mem_db,
            d,
            {"L1": {"momentum_score": 70}, "L2": {"momentum_score": 0}, "S1": {}},
        )
        _insert_snapshot(mem_db, d, [*_BOOK, ("L2", "LONG", None)])
        assert _load_history_spreads(mem_db, _TODAY)["momentum_score"] == pytest.approx([20])

    def test_all_null_weights_default_to_neutral(self, mem_db):
        # an all-null column loads as object dtype; both sides fall back to 50.0
        d = _past_dates(1)[0]
        _momentum_day(mem_db, d, 20)
        _insert_snapshot(mem_db, d, [("L1", "LONG", None), ("S1", "SHORT", None)])
        assert _load_history_spreads(mem_db, _TODAY)["momentum_score"] == pytest.approx([0])

    def test_no_history_returns_empty_lists(self, mem_db):
        assert _load_history_spreads(mem_db, _TODAY) == {f: [] for f in _FACTORS}


# ---------------------------------------------------------------------------
# AC3 — insufficient history
# ---------------------------------------------------------------------------


class TestInsufficientHistory:
    def test_nine_dates_skip(self, mem_db):
        _history(mem_db, list(range(10, 19)))
        _momentum_day(mem_db, _TODAY, 90)
        alerts = run_factor_monitor(mem_db, _positions(), _TODAY, _config())
        assert alerts == []
        assert _risk_rows(mem_db) == [
            ("OK", "insufficient history for z-scoring (9 snapshot dates)")
        ]

    def test_ten_dates_score(self, mem_db):
        _history(mem_db, list(range(10, 20)))
        _momentum_day(mem_db, _TODAY, 90)
        alerts = run_factor_monitor(mem_db, _positions(), _TODAY, _config())
        assert [a["factor"] for a in alerts] == ["momentum_score"]


# ---------------------------------------------------------------------------
# AC4 — z-score
# ---------------------------------------------------------------------------


class TestZScore:
    _SPREADS = list(range(10, 20))  # mean 14.5, sample std ~3.028
    _STD = pd.Series(range(10, 20)).std(ddof=1)

    def test_spread_at_mean_gives_zero_and_constant_factors_skipped(self, mem_db):
        _history(mem_db, self._SPREADS)
        _momentum_day(mem_db, _TODAY, 14.5)
        # threshold below zero exposes every z that was computed
        alerts = run_factor_monitor(mem_db, _positions(), _TODAY, _config(-1), whatif=True)
        assert [a["factor"] for a in alerts] == ["momentum_score"]
        assert alerts[0]["z"] == pytest.approx(0.0)

    def test_two_std_above_mean_alerts(self, mem_db):
        _history(mem_db, self._SPREADS)
        _momentum_day(mem_db, _TODAY, 14.5 + 2 * self._STD)
        alerts = run_factor_monitor(mem_db, _positions(), _TODAY, _config(), whatif=True)
        assert len(alerts) == 1
        assert alerts[0]["z"] == pytest.approx(2.0, abs=1e-3)

    def test_within_threshold_no_alert(self, mem_db):
        _history(mem_db, self._SPREADS)
        _momentum_day(mem_db, _TODAY, 14.5 + self._STD)
        assert run_factor_monitor(mem_db, _positions(), _TODAY, _config(), whatif=True) == []


# ---------------------------------------------------------------------------
# AC5 — priority and alert logging unchanged
# ---------------------------------------------------------------------------


class TestPriority:
    def _alert_for(self, mem_db, flagged):
        _history(mem_db, list(range(10, 20)))
        _momentum_day(mem_db, _TODAY, 90)
        mem_db.execute(
            crowding_flags.insert().values(
                score_date=_TODAY,
                factor_a="momentum_score",
                factor_b="value_score",
                flagged=flagged,
            )
        )
        mem_db.commit()
        return run_factor_monitor(mem_db, _positions(), _TODAY, _config())

    def test_crowded_factor_is_high(self, mem_db):
        alerts = self._alert_for(mem_db, flagged=1)
        assert alerts[0]["priority"] == "HIGH"
        result, reason = _risk_rows(mem_db)[0]
        assert result == "WARNING"
        assert reason.startswith("FACTOR_SPREAD momentum_score z=")
        assert reason.endswith("priority=HIGH")

    def test_unflagged_pair_is_medium(self, mem_db):
        assert self._alert_for(mem_db, flagged=0)[0]["priority"] == "MEDIUM"


# ---------------------------------------------------------------------------
# AC6 — regression: 2026-10-22 shape
# ---------------------------------------------------------------------------


class TestEmptyBookRegression:
    @pytest.mark.parametrize("whatif", [True, False])
    def test_empty_positions_with_long_score_history(self, mem_db, whatif):
        # 117 score dates of a spread-out universe whose top half drifts a little day to day,
        # no portfolio snapshots, empty book
        for k, d in enumerate([*_past_dates(116), _TODAY]):
            universe = {
                f"T{i:02d}": dict.fromkeys(_FACTORS, i * 2.5 + (k % 5) * 0.2 * (i >= 20))
                for i in range(40)
            }
            _insert_scores(mem_db, d, universe)
        cols = ["ticker", "direction", "weight"]
        alerts = run_factor_monitor(
            mem_db, pd.DataFrame(columns=cols), _TODAY, _config(), whatif=whatif
        )
        assert alerts == []
