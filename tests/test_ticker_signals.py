"""Tests for dashboard/ticker_signals.py — packet 18 AC3, AC4."""

from __future__ import annotations

import math
from types import SimpleNamespace

import pandas as pd
import pytest

from dashboard.theme import LONG_COL, NEUTRAL, SHORT_COL
from dashboard.ticker_signals import (
    SUBFACTORS,
    cluster_lines,
    direction_colour,
    fmt_score,
    subfactor_rows,
    trend_figure,
)
from factors.db import factor_scores

_COMPOSITES = {
    "momentum_score",
    "value_score",
    "quality_score",
    "growth_score",
    "revisions_score",
    "short_interest_score",
    "insider_score",
    "institutional_score",
    "composite_score",
}
_NON_SCORE = {"ticker", "score_date", "sector", "regime", "direction", "computed_at"}


class TestSubfactors:
    def test_covers_every_subfactor_column(self):
        expected = set(factor_scores.columns.keys()) - _COMPOSITES - _NON_SCORE
        assert {col for _, col, _ in SUBFACTORS} == expected
        assert len(SUBFACTORS) == 36

    def test_rows_carry_factor_label_and_score(self):
        rows = subfactor_rows({"mom_12_1": 81.5, "qual_piotroski": 40.0})
        mom = next(r for r in rows if r["Sub-factor"] == "12-1 month return")
        assert mom["Factor"] == "Momentum"
        assert mom["Score"] == pytest.approx(81.5)
        assert len(rows) == 36

    def test_missing_and_nan_are_none(self):
        # Adversarial: NaN from pandas-written rows must not render as "nan"
        rows = subfactor_rows({"mom_12_1": float("nan")})
        assert all(r["Score"] is None for r in rows)


class TestFormatting:
    @pytest.mark.parametrize("value", [None, float("nan"), math.inf * 0])
    def test_fmt_score_blank(self, value):
        assert fmt_score(value) == "—"

    def test_fmt_score_number(self):
        assert fmt_score(18.666) == "19"

    def test_direction_colour(self):
        assert direction_colour("LONG") == LONG_COL
        assert direction_colour("SHORT") == SHORT_COL
        assert direction_colour("NEUTRAL") == NEUTRAL
        assert direction_colour(None) == NEUTRAL


class TestTrendFigure:
    def _df(self, dates):
        return pd.DataFrame({"date": dates, "a": range(len(dates)), "b": range(len(dates))})

    def test_two_dates_drawn(self):
        fig = trend_figure(self._df(["2026-09-01", "2026-10-01"]), "a", "A", "b", "B")
        assert fig is not None
        assert len(fig.data) == 2
        assert fig.data[1].yaxis == "y2"

    def test_one_row_skipped(self):
        assert trend_figure(self._df(["2026-09-01"]), "a", "A", "b", "B") is None

    def test_duplicate_dates_count_once(self):
        # Adversarial: two rows on the same date are still one point in time
        assert trend_figure(self._df(["2026-09-01", "2026-09-01"]), "a", "A", "b", "B") is None


class TestClusterLines:
    def test_format_and_order_preserved(self):
        flags = [
            SimpleNamespace(
                window_start="2026-09-30",
                window_end="2026-10-30",
                insider_count=3,
                total_shares=604.0,
            ),
            SimpleNamespace(
                window_start="2026-06-30",
                window_end="2026-07-30",
                insider_count=10,
                total_shares=None,
            ),
        ]
        assert cluster_lines(flags) == [
            "Insider cluster: 2026-09-30 to 2026-10-30, 3 insiders, 604 shares",
            "Insider cluster: 2026-06-30 to 2026-07-30, 10 insiders, 0 shares",
        ]
