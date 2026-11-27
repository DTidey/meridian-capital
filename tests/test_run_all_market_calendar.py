"""run_all.py market-day check before Layer 1 (packet 68)."""

from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

import run_all
from execution import market_calendar
from factors import score_date as score_date_mod

SKIP_LINE = "Market closed on {d} ({reason}): skipping the nightly run"


def _entry(day: date, close_hour_utc: int = 21):
    return SimpleNamespace(
        date=day,
        open=datetime(day.year, day.month, day.day, 14, 30, tzinfo=UTC),
        close=datetime(day.year, day.month, day.day, close_hour_utc, 0, tzinfo=UTC),
    )


class _Alpaca:
    """Fake Alpaca client factory; records calendar requests."""

    def __init__(self, trading_days=(), error=None, close_hour_utc=21):
        self.trading_days = set(trading_days)
        self.error = error
        self.close_hour_utc = close_hour_utc
        self.requests = []
        self._session = requests.Session()  # as alpaca-py's RESTClient has

    def __call__(self):
        if self.error:
            raise self.error
        return self

    def get_calendar(self, filters=None):
        self.requests.append(filters.start)
        return [_entry(d, self.close_hour_utc) for d in self.trading_days if d == filters.start]


def _run(monkeypatch, score_date, alpaca, argv=(), cmds=None):
    """Run run_all.main with layers faked; return (labels run, exit code)."""
    labels = []

    def fake_run(label, cmd, skip=False):
        labels.append(label)
        if cmds is not None:
            cmds[label] = cmd
        return True

    monkeypatch.setattr(run_all, "_run", fake_run)
    monkeypatch.setattr(run_all, "_load_config", lambda: {"scoring": {"score_date": score_date}})
    if score_date is not None:  # the fixed score date is "today" here (historical: own tests)
        monkeypatch.setattr(run_all, "run_date", lambda: score_date)
    monkeypatch.setattr(market_calendar, "get_client", alpaca)
    monkeypatch.setattr("sys.argv", ["run_all.py", "--no-filings", "--no-13f", *argv])
    try:
        run_all.main()
        code = 0
    except SystemExit as exc:
        code = exc.code or 0
    return labels, code


def test_holiday_skips_with_exit_0(monkeypatch, capsys):
    alpaca = _Alpaca(trading_days=[date(2026, 11, 25), date(2026, 11, 27)])
    labels, code = _run(monkeypatch, "2026-11-26", alpaca)
    assert code == 0
    assert labels == []
    assert SKIP_LINE.format(d="2026-11-26", reason="holiday") in capsys.readouterr().out
    assert alpaca.requests == [date(2026, 11, 26)]


def test_weekend_skips_without_client(monkeypatch, capsys):
    alpaca = _Alpaca(error=AssertionError("no Alpaca client on a weekend"))
    labels, code = _run(monkeypatch, "2026-11-28", alpaca)
    assert (labels, code) == ([], 0)
    assert SKIP_LINE.format(d="2026-11-28", reason="weekend") in capsys.readouterr().out


def test_trading_day_runs(monkeypatch, capsys):
    alpaca = _Alpaca(trading_days=[date(2026, 11, 30)])
    labels, code = _run(monkeypatch, "2026-11-30", alpaca)
    assert code == 0
    assert labels[0].startswith("Layer 1") and labels[-1].startswith("Layer 7")
    out = capsys.readouterr().out
    assert "Market open on 2026-11-30" in out
    assert "skipping" not in out


def test_half_day_runs(monkeypatch):
    alpaca = _Alpaca(trading_days=[date(2026, 11, 27)], close_hour_utc=18)  # 13:00 ET
    labels, code = _run(monkeypatch, "2026-11-27", alpaca)
    assert code == 0
    assert labels[0].startswith("Layer 1")


@pytest.mark.parametrize(
    "error",
    [
        ConnectionError("calendar timed out"),
        OSError("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in environment."),
    ],
)
def test_calendar_error_runs_with_warning(monkeypatch, capsys, error):
    labels, code = _run(monkeypatch, "2026-11-26", _Alpaca(error=error))
    assert labels[0].startswith("Layer 1")
    out = capsys.readouterr().out
    assert "WARNING: market calendar check failed for 2026-11-26" in out
    assert str(error) in out
    assert "skipping" not in out


def test_override_runs_on_holiday(monkeypatch, capsys):
    alpaca = _Alpaca(trading_days=[])
    labels, code = _run(monkeypatch, "2026-11-26", alpaca, ["--ignore-market-calendar"])
    assert labels[0].startswith("Layer 1")
    assert alpaca.requests == []
    assert "--ignore-market-calendar" in capsys.readouterr().out


def test_checks_the_layer2_score_date(monkeypatch):
    """AC7: the date checked is the date resolve_score_date gives Layer 2."""
    alpaca = _Alpaca(trading_days=[date(2026, 11, 20)])
    _run(monkeypatch, "2026-11-20", alpaca)
    assert alpaca.requests == [date(2026, 11, 20)]


def test_run_all_doc_mentions_override():
    doc = (Path(run_all.__file__).parent / "docs" / "run_all.md").read_text()
    assert "--ignore-market-calendar" in doc
    assert "Market closed on" in doc


# Adversarial cases


def test_whatif_and_tickers_skipped_on_holiday(monkeypatch):
    labels, code = _run(
        monkeypatch, "2026-11-26", _Alpaca(), ["--whatif", "--tickers", "AAPL", "MSFT"]
    )
    assert (labels, code) == ([], 0)


def test_bad_config_score_date_fails_open(monkeypatch, capsys):
    labels, _ = _run(monkeypatch, "26/11/2026", _Alpaca(trading_days=[]))
    assert labels[0].startswith("Layer 1")
    assert "WARNING: market calendar check failed" in capsys.readouterr().out


def test_response_for_other_dates_fails_open(monkeypatch, capsys):
    class _Wrong(_Alpaca):
        def get_calendar(self, filters=None):
            return [_entry(date(2026, 11, 25))]

    labels, _ = _run(monkeypatch, "2026-11-26", _Wrong())
    assert labels[0].startswith("Layer 1")
    assert "WARNING: market calendar check failed" in capsys.readouterr().out


def test_unexpected_exception_type_fails_open(monkeypatch, capsys):
    labels, _ = _run(monkeypatch, "2026-11-30", _Alpaca(error=RuntimeError("boom")))
    assert labels[0].startswith("Layer 1")
    assert "WARNING: market calendar check failed" in capsys.readouterr().out


# One score date per run (review fix): checked date == the date every scoring layer gets.

_DATED_STEPS = (
    "Layer 2",
    "Layer 3",
    "Book sync",
    "Layer 4",
    "Layer 5",
    "Layer 6",
)


def _date_of(cmd):
    return cmd[cmd.index("--date") + 1]


class _MidnightDate(date):
    """date.today() returns Thu 2026-11-26 on the first call, Fri 11-27 on every later call."""

    calls = 0

    @classmethod
    def today(cls):
        cls.calls += 1
        return cls(2026, 11, 26) if cls.calls == 1 else cls(2026, 11, 27)


@pytest.fixture
def crossing_midnight(monkeypatch):
    _MidnightDate.calls = 0
    monkeypatch.setattr(score_date_mod, "date", _MidnightDate)


def test_midnight_crossing_layer2_gets_checked_date(monkeypatch, crossing_midnight):
    alpaca = _Alpaca(trading_days=[date(2026, 11, 26), date(2026, 11, 27)])
    cmds = {}
    _run(monkeypatch, None, alpaca, cmds=cmds)
    assert alpaca.requests == [date(2026, 11, 26)]
    layer2 = next(cmd for label, cmd in cmds.items() if label.startswith("Layer 2"))
    assert _date_of(layer2) == "2026-11-26"
    assert _MidnightDate.calls == 1  # resolved once; no second read of the clock


def test_every_dated_layer_gets_the_one_score_date(monkeypatch, crossing_midnight):
    cmds = {}
    _run(monkeypatch, None, _Alpaca(trading_days=[date(2026, 11, 26)]), cmds=cmds)
    for step in _DATED_STEPS:
        cmd = next(c for label, c in cmds.items() if label.startswith(step))
        assert _date_of(cmd) == "2026-11-26", step
    layer1 = next(c for label, c in cmds.items() if label.startswith("Layer 1"))
    layer7 = next(c for label, c in cmds.items() if label.startswith("Layer 7"))
    assert "--date" not in layer1 and "--date" not in layer7


def test_whatif_status_gets_the_score_date(monkeypatch):
    cmds = {}
    _run(monkeypatch, "2026-11-30", _Alpaca(trading_days=[date(2026, 11, 30)]), ["--whatif"], cmds)
    layer6 = next(c for label, c in cmds.items() if label.startswith("Layer 6"))
    assert "--status" in layer6 and _date_of(layer6) == "2026-11-30"


def test_override_passes_the_same_date(monkeypatch, crossing_midnight, capsys):
    alpaca = _Alpaca()
    cmds = {}
    _run(monkeypatch, None, alpaca, ["--ignore-market-calendar"], cmds)
    assert alpaca.requests == []
    for step in _DATED_STEPS:
        cmd = next(c for label, c in cmds.items() if label.startswith(step))
        assert _date_of(cmd) == "2026-11-26", step
    assert "skipped for 2026-11-26" in capsys.readouterr().out


def test_stalled_calendar_warns_and_runs(monkeypatch, capsys):
    """A calendar that never answers times out and falls back to a normal run (AC5)."""
    import socket

    from alpaca.trading.client import TradingClient

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    url = f"http://127.0.0.1:{srv.getsockname()[1]}"
    monkeypatch.setattr(market_calendar, "CALENDAR_TIMEOUT", (0.5, 0.5))
    try:
        labels, code = _run(
            monkeypatch, "2026-11-30", lambda: TradingClient("k", "s", url_override=url)
        )
    finally:
        srv.close()
    assert code == 0 and labels[0].startswith("Layer 1")
    out = capsys.readouterr().out
    assert "WARNING: market calendar check failed for 2026-11-30" in out
    assert "Timeout" in out
