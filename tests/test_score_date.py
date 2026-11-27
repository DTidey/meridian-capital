"""factors/score_date.py: one score-date rule for run_all and Layer 2 (packet 68, AC7)."""

from datetime import date

import pytest

import run_all
import run_scoring
from factors import score_date as score_date_mod
from factors.score_date import resolve_score_date


class _FakeDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 11, 26)


@pytest.fixture
def fixed_today(monkeypatch):
    monkeypatch.setattr(score_date_mod, "date", _FakeDate)


def test_cli_date_wins(fixed_today):
    assert resolve_score_date("2026-11-20", {"score_date": "2026-11-10"}) == "2026-11-20"


def test_config_date_next(fixed_today):
    assert resolve_score_date(None, {"score_date": "2026-11-10"}) == "2026-11-10"


def test_today_last(fixed_today):
    assert resolve_score_date(None, {"score_date": None}) == "2026-11-26"
    assert resolve_score_date(None, {}) == "2026-11-26"


def test_empty_cli_date_falls_through(fixed_today):
    assert resolve_score_date("", {}) == "2026-11-26"


def test_run_all_and_layer2_share_the_function():
    assert run_all.resolve_score_date is resolve_score_date
    assert run_scoring.resolve_score_date is resolve_score_date


def test_layer2_scores_the_resolved_date(monkeypatch):
    """run_scoring.main passes its --date and config through resolve_score_date."""
    seen = []

    class _Stop(Exception):
        pass

    def spy(cli_date, scoring_cfg):
        seen.append((cli_date, scoring_cfg.get("score_date")))
        raise _Stop

    monkeypatch.setattr(run_scoring, "resolve_score_date", spy)
    monkeypatch.setattr(run_scoring, "_setup_logging", lambda config: None)
    monkeypatch.setattr("sys.argv", ["run_scoring.py", "--date", "2026-11-26"])
    with pytest.raises(_Stop):
        run_scoring.main()
    assert seen == [("2026-11-26", None)]
