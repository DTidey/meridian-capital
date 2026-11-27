"""run_all.py market-day check before Layer 1 (packet 68)."""

from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import run_all
from execution import market_calendar

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

    def __call__(self):
        if self.error:
            raise self.error
        return self

    def get_calendar(self, filters=None):
        self.requests.append(filters.start)
        return [_entry(d, self.close_hour_utc) for d in self.trading_days if d == filters.start]


def _run(monkeypatch, score_date: str, alpaca: _Alpaca, argv=()):
    """Run run_all.main with layers faked; return (labels run, exit code)."""
    labels = []

    def fake_run(label, cmd, skip=False):
        labels.append(label)
        return True

    monkeypatch.setattr(run_all, "_run", fake_run)
    monkeypatch.setattr(run_all, "_load_config", lambda: {"scoring": {"score_date": score_date}})
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
