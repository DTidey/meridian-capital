"""Tests for factors/revisions.py."""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from factors.revisions import COLS, compute

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _estimates(ticker, n_days=90, eps_start=5.0, eps_growth=0.01):
    dates = pd.date_range("2024-01-01", periods=n_days, freq="D")
    eps = [eps_start + eps_growth * i for i in range(n_days)]
    return pd.DataFrame(
        {
            "ticker": ticker,
            "date": dates,
            "eps_estimate_fwd": eps,
            "price_target": 120.0,
            "num_analysts": 10,
        }
    )


def _make_data(tickers, est_frames=None):
    universe = pd.DataFrame([(t, "IT") for t in tickers], columns=["ticker", "sector"])
    if est_frames is None:
        est_frames = {t: _estimates(t) for t in tickers}
    estimates = (
        pd.concat(list(est_frames.values()), ignore_index=True)
        if est_frames
        else pd.DataFrame(
            columns=["ticker", "date", "eps_estimate_fwd", "price_target", "num_analysts"]
        )
    )
    return {"universe": universe, "estimates": estimates}


_CONFIG = {"scoring": {"min_sector_size": 2}}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDegenerateMode:
    def test_no_history_yields_50(self):
        data = _make_data(["AAPL"], est_frames={})
        result = compute(data, _CONFIG)
        for col in COLS + ["revisions_score"]:
            assert result.loc["AAPL", col] == pytest.approx(50.0)

    def test_short_history_yields_50(self):
        # Only 10 days of data — below 30-day threshold
        data = _make_data(["AAPL"], est_frames={"AAPL": _estimates("AAPL", n_days=10)})
        result = compute(data, _CONFIG)
        for col in COLS:
            assert result.loc["AAPL", col] == pytest.approx(50.0)

    def test_empty_estimates_returns_all_50(self):
        data = _make_data(["AAPL", "MSFT"], est_frames={})
        result = compute(data, _CONFIG)
        assert (result == 50.0).all().all()


class TestRevisionDeltas:
    def test_positive_revision_scores_higher_than_negative(self):
        # UPWARD: eps rising over 90 days
        # DOWNWARD: eps falling over 90 days
        data = _make_data(
            ["UP", "DOWN"],
            est_frames={
                "UP": _estimates("UP", eps_start=5.0, eps_growth=0.05),
                "DOWN": _estimates("DOWN", eps_start=5.0, eps_growth=-0.05),
            },
        )
        result = compute(data, _CONFIG)
        assert result.loc["UP", "rev_30d"] > result.loc["DOWN", "rev_30d"]
        assert result.loc["UP", "rev_60d"] > result.loc["DOWN", "rev_60d"]

    def test_30d_delta_uses_30_day_lookback(self):
        # Flat for first 60 days, then big jump in last 30
        dates = pd.date_range("2024-01-01", periods=91, freq="D")
        eps = [5.0] * 61 + [6.0] * 30
        est = pd.DataFrame(
            {
                "ticker": "JUMP",
                "date": dates,
                "eps_estimate_fwd": eps,
                "price_target": 120.0,
                "num_analysts": 5,
            }
        )
        data = _make_data(
            ["JUMP", "FLAT"],
            est_frames={
                "JUMP": est,
                "FLAT": _estimates("FLAT", eps_start=5.0, eps_growth=0.0),
            },
        )
        result = compute(data, _CONFIG)
        # JUMP should have much higher 30d revision than FLAT
        assert result.loc["JUMP", "rev_30d"] > result.loc["FLAT", "rev_30d"]


class TestStructure:
    def test_columns_present(self):
        data = _make_data(["AAPL", "MSFT"])
        result = compute(data, _CONFIG)
        assert set(COLS + ["revisions_score"]).issubset(result.columns)

    def test_scores_0_to_100(self):
        data = _make_data(["AAPL", "MSFT", "GOOG"])
        result = compute(data, _CONFIG)
        for col in COLS + ["revisions_score"]:
            assert result[col].between(0, 100).all(), f"{col} out of range"

    def test_equal_revisions_both_get_50(self):
        data = _make_data(
            ["A", "B"],
            est_frames={
                "A": _estimates("A", eps_start=5.0, eps_growth=0.01),
                "B": _estimates("B", eps_start=5.0, eps_growth=0.01),
            },
        )
        result = compute(data, _CONFIG)
        # Identical revision paths → both rank at 50
        for col in COLS:
            assert result.loc["A", col] == pytest.approx(result.loc["B", col])


# ---------------------------------------------------------------------------
# Packet 40: Friday 90-day window (AC7) and prior-snapshot tolerance (AC8)
# ---------------------------------------------------------------------------

_GROWTH = {"AAA": 0.01, "BBB": 0.02, "CCC": 0.03}


def _insert_weekday_snapshots(conn, end, start, skip=()):
    from data.db import analyst_estimates

    for i, d in enumerate(pd.bdate_range(start, end)):
        if d.strftime("%Y-%m-%d") in skip:
            continue
        for ticker, growth in _GROWTH.items():
            conn.execute(
                analyst_estimates.insert().values(
                    ticker=ticker,
                    date=d.strftime("%Y-%m-%d"),
                    eps_estimate_fwd=5.0 + growth * i,
                )
            )
    conn.commit()


def _score_from_loader(conn, score_date):
    from datetime import date

    from factors.loader import _load_estimates

    estimates = _load_estimates(conn, date.fromisoformat(score_date))
    return compute(_make_data(list(_GROWTH), est_frames={"x": estimates}), _CONFIG)


class TestFridayWindow:
    def test_rev_90d_computed_on_friday(self, tmp_db):
        _insert_weekday_snapshots(tmp_db, "2026-10-16", "2026-04-01")
        result = _score_from_loader(tmp_db, "2026-10-16")  # Friday; 90-day cutoff is a Saturday
        assert result["rev_90d"].tolist() == pytest.approx([100 / 3, 200 / 3, 100.0])

    def test_thanksgiving_friday_cutoff_uses_wednesday(self, tmp_db):
        # 2027-02-26 minus 90 days is Saturday 2026-11-28; no snapshots on Thu 26 or Fri 27.
        _insert_weekday_snapshots(
            tmp_db, "2027-02-26", "2026-09-01", skip={"2026-11-26", "2026-11-27"}
        )
        result = _score_from_loader(tmp_db, "2027-02-26")
        assert result["rev_90d"].tolist() == pytest.approx([100 / 3, 200 / 3, 100.0])


def _daily(ticker, growth, days):
    base = pd.Timestamp("2024-01-01")
    return pd.DataFrame(
        {
            "ticker": ticker,
            "date": [base + pd.Timedelta(days=d) for d in days],
            "eps_estimate_fwd": [5.0 + growth * d for d in days],
            "price_target": 120.0,
            "num_analysts": 10,
        }
    )


def _tolerance_data(gap_days):
    frames = {t: _daily(t, g, range(120)) for t, g in _GROWTH.items()}
    # Ref date is day 119, so the 90-day cutoff is day 29; DDD's latest prior is day 29 - gap.
    frames["DDD"] = _daily("DDD", 0.05, [*range(30 - gap_days), *range(38, 120)])
    return _make_data([*_GROWTH, "DDD"], est_frames=frames)


class TestPriorTolerance:
    def test_prior_more_than_7_days_before_cutoff_not_used(self):
        result = compute(_tolerance_data(gap_days=8), _CONFIG)
        assert result.loc["DDD", "rev_90d"] == pytest.approx(50.0)
        assert result.loc["CCC", "rev_90d"] == pytest.approx(100.0)

    # Adversarial: exactly 7 days before the cutoff is still inside the tolerance.
    def test_prior_exactly_7_days_before_cutoff_used(self):
        result = compute(_tolerance_data(gap_days=7), _CONFIG)
        assert result.loc["DDD", "rev_90d"] == pytest.approx(100.0)

    # Adversarial: the gap only blanks the window it affects; the 30-day window still scores.
    def test_other_windows_unaffected(self):
        result = compute(_tolerance_data(gap_days=8), _CONFIG)
        assert result.loc["DDD", "rev_30d"] == pytest.approx(100.0)
