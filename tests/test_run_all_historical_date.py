"""run_all.py never syncs or executes against a historical score date (packet 68, AC12).

Book sync (execution/book_sync.py sync_book) stores today's broker book and NAV under the
date it is given, so a backfill scoring.score_date must never reach it or Layer 6.
"""

from datetime import date

import pytest

import run_all
from execution import market_calendar
from factors import score_date as score_date_mod

RUN_DAY = "2026-11-27"  # Friday: an open day in every test here


class _OpenAlpaca:
    """Fake Alpaca client: every weekday is a trading day."""

    def __init__(self):
        import requests

        self._session = requests.Session()

    def __call__(self):
        return self

    def get_calendar(self, filters=None):
        from types import SimpleNamespace

        return [SimpleNamespace(date=filters.start)]


def _pipeline(monkeypatch, score_date, argv=(), pin_run_day=True):
    """Run run_all.main with real command building; broker steps feed a fake sync_book.

    Returns (labels actually run, sync_book dates, execute dates, exit code).
    """
    ran, synced, executed = [], [], []

    def fake_sync_book(score_date_arg):
        synced.append(score_date_arg)

    def fake_run(label, cmd, skip=False):
        if skip:
            return True
        ran.append(label)
        if cmd[1] == "run_execution.py":
            # run_execution.py: --date, else date.today() (its argparse default)
            day = cmd[cmd.index("--date") + 1] if "--date" in cmd else RUN_DAY
            if "--sync" in cmd:
                fake_sync_book(day)
            if "--execute" in cmd:
                executed.append(day)
        return True

    monkeypatch.setattr(run_all, "_run", fake_run)
    monkeypatch.setattr(run_all, "_load_config", lambda: {"scoring": {"score_date": score_date}})
    if pin_run_day:
        # raising=False: on code without run_date the test fails on its assertions instead
        monkeypatch.setattr(run_all, "run_date", lambda: RUN_DAY, raising=False)
    monkeypatch.setattr(market_calendar, "get_client", _OpenAlpaca())
    monkeypatch.setattr("sys.argv", ["run_all.py", "--no-filings", "--no-13f", *argv])
    try:
        run_all.main()
        code = 0
    except SystemExit as exc:
        code = exc.code or 0
    return ran, synced, executed, code


@pytest.mark.parametrize(
    "argv",
    [
        (),
        ("--whatif",),
        ("--dry-run",),
        ("--ignore-market-calendar",),
        ("--no-execution",),
        ("--no-execution", "--whatif"),
        ("--no-execution", "--no-reporting", "--tickers", "AAPL"),
    ],
)
def test_historical_date_refused_before_any_layer(monkeypatch, capsys, argv):
    ran, synced, executed, code = _pipeline(monkeypatch, "2026-11-20", argv)
    assert code == 1
    assert ran == [] and synced == [] and executed == []
    out = capsys.readouterr().out
    assert "ERROR: score date 2026-11-20 is not the run date 2026-11-27" in out
    assert "Pipeline failed at: Score date check" in out


def test_future_date_refused(monkeypatch):
    ran, synced, _, code = _pipeline(monkeypatch, "2026-12-01")
    assert (ran, synced, code) == ([], [], 1)


@pytest.mark.parametrize("configured", [None, RUN_DAY])
def test_run_date_is_synced_and_executed(monkeypatch, configured):
    ran, synced, executed, code = _pipeline(monkeypatch, configured)
    assert code == 0
    assert synced == [RUN_DAY] and executed == [RUN_DAY]


class _MidnightDate(date):
    """date.today(): Fri 2026-11-27 on the first call, Sat 11-28 afterwards."""

    calls = 0

    @classmethod
    def today(cls):
        cls.calls += 1
        return cls(2026, 11, 27) if cls.calls == 1 else cls(2026, 11, 28)


def test_midnight_crossing_is_not_historical(monkeypatch):
    """The run date is captured once: a run passing midnight still syncs the checked date."""
    _MidnightDate.calls = 0
    monkeypatch.setattr(score_date_mod, "date", _MidnightDate)
    ran, synced, executed, code = _pipeline(monkeypatch, None, pin_run_day=False)
    assert code == 0
    assert synced == ["2026-11-27"] and executed == ["2026-11-27"]
    assert _MidnightDate.calls == 1
