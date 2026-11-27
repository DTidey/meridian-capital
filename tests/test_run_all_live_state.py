"""A historical run_all never touches live risk state (packet 68 review 4, AC12).

Real side effects: run_all's real command building drives the real Layer 5 circuit-breaker
sequence (load_risk_state -> run_circuit_breakers -> save_risk_state, as run_risk_check.run
does) against a temp cache dir and a temp database whose NAV history has a 10% drawdown on the
historical date. On the run date the drawdown is 0, so the control run must not halt.
"""

import contextlib
import json

import pytest
import sqlalchemy as sa

import analysis.db  # noqa: F401
import execution.db  # noqa: F401
import portfolio.db  # noqa: F401
import risk.db  # noqa: F401
import run_all
import run_risk_check
from execution import market_calendar
from reporting.db import portfolio_nav
from risk.circuit_breakers import run_circuit_breakers
from risk.risk_state import load_risk_state, save_risk_state

RUN_DAY = "2026-11-27"
PAST = "2026-11-20"
LIVE_STATE = {"as_of": "2026-11-26", "nav_usd": 100000.0, "marker": "live"}


class _OpenAlpaca:
    def __init__(self):
        import requests

        self._session = requests.Session()

    def __call__(self):
        return self

    def get_calendar(self, filters=None):
        from types import SimpleNamespace

        return [SimpleNamespace(date=filters.start)]


@pytest.fixture
def live_cache(tmp_path):
    """The shared cache dir as a live run leaves it: risk_state.json present, no halt.lock."""
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "risk_state.json").write_text(json.dumps(LIVE_STATE))
    return cache


@pytest.fixture
def nav_db(tmp_engine):
    with tmp_engine.begin() as conn:
        for d, nav in [("2026-11-10", 100_000.0), (PAST, 90_000.0), (RUN_DAY, 100_000.0)]:
            conn.execute(sa.insert(portfolio_nav).values(date=d, nav=nav))
    return tmp_engine


def _run_all(monkeypatch, score_date, argv, cache, engine, config):
    """run_all.main with real commands; Layer 5 runs its real circuit-breaker sequence.

    Returns {script: [cmd, ...]} for every step actually run (not skipped).
    """
    started: dict[str, list] = {}

    def layer5(cmd):
        args = run_risk_check._parse_args(cmd[2:])
        date = args.date or RUN_DAY  # run_risk_check: --date, else today
        with engine.connect() as conn:
            state = load_risk_state(cache)
            state = run_circuit_breakers(
                conn, date, 100_000.0, config, state, cache, whatif=args.whatif
            )
            save_risk_state(state, cache)  # run_risk_check.run saves even under --whatif

    def fake_run(label, cmd, skip=False):
        if skip:
            return True
        started.setdefault(cmd[1], []).append(cmd)
        if cmd[1] == "run_risk_check.py":
            layer5(cmd)
        return True

    monkeypatch.setattr(run_all, "_run", fake_run)
    monkeypatch.setattr(run_all, "_load_config", lambda: {"scoring": {"score_date": score_date}})
    monkeypatch.setattr(run_all, "run_date", lambda: RUN_DAY, raising=False)
    monkeypatch.setattr(market_calendar, "get_client", _OpenAlpaca())
    monkeypatch.setattr("sys.argv", ["run_all.py", "--no-filings", "--no-13f", *argv])
    with contextlib.suppress(SystemExit):
        run_all.main()
    return started


@pytest.mark.parametrize("argv", [("--no-execution",), ("--no-execution", "--whatif"), ()], ids=str)
def test_historical_run_leaves_halt_lock_and_risk_state_alone(
    monkeypatch, live_cache, nav_db, config, argv
):
    started = _run_all(monkeypatch, PAST, argv, live_cache, nav_db, config)
    assert not (live_cache / "halt.lock").exists()
    assert json.loads((live_cache / "risk_state.json").read_text()) == LIVE_STATE
    # Neither Layer 4 (--rebalance or --whatif) nor Layer 5 is reached for a past date
    assert "run_portfolio.py" not in started
    assert "run_risk_check.py" not in started


def test_run_date_control_writes_live_state_without_halting(
    monkeypatch, live_cache, nav_db, config
):
    """The harness is real: on the run date Layer 5 does update risk_state.json."""
    started = _run_all(monkeypatch, None, ["--no-execution"], live_cache, nav_db, config)
    assert started["run_risk_check.py"][0][2:4] == ["--date", RUN_DAY]
    assert "--rebalance" in started["run_portfolio.py"][0]
    state = json.loads((live_cache / "risk_state.json").read_text())
    assert state.get("marker") == "live" and state["drawdown_pct"] == 0.0
    assert not (live_cache / "halt.lock").exists()


def test_harness_halts_on_the_historical_drawdown(live_cache, nav_db, config):
    """Without the guard, Layer 5 on the past date (10% drawdown) writes the shared halt lock."""
    with nav_db.connect() as conn:
        run_circuit_breakers(conn, PAST, 90_000.0, config, {}, live_cache, whatif=False)
    assert (live_cache / "halt.lock").exists()
