"""run_scoring.py freshness gate: no scoring without a SPY bar dated score_date (packet 38)."""

import logging

import pytest
import sqlalchemy as sa

import run_scoring
from data.db import daily_prices, get_engine, initialise_schema
from factors.db import factor_scores, regime_state


class _Reached(Exception):
    """Raised by the stubbed data loader: scoring got past the gate."""


@pytest.fixture
def scoring_db(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'scoring.db'}"
    engine = get_engine(url)
    initialise_schema(engine)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr(run_scoring, "database_url", lambda: url)
    monkeypatch.setattr(run_scoring, "_setup_logging", lambda config: None)
    monkeypatch.setattr(run_scoring, "load_scoring_data", _raise_reached)
    yield engine
    engine.dispose()


def _raise_reached(*args, **kwargs):
    raise _Reached


def _bars(engine, ticker, dates, close=100.0):
    with engine.begin() as conn:
        for d in dates:
            conn.execute(sa.insert(daily_prices).values(ticker=ticker, date=d, close=close))


def _score(monkeypatch, score_date):
    monkeypatch.setattr("sys.argv", ["run_scoring.py", "--date", score_date])
    run_scoring.main()


def _count(engine, table):
    with engine.connect() as conn:
        return conn.execute(sa.select(sa.func.count()).select_from(table)).scalar()


def test_stale_spy_stops_scoring(scoring_db, monkeypatch, caplog):
    _bars(scoring_db, "SPY", ["2026-11-24", "2026-11-25"])
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
        _score(monkeypatch, "2026-11-26")
    assert exc.value.code != 0
    assert "2026-11-25" in caplog.text and "2026-11-26" in caplog.text
    assert _count(scoring_db, factor_scores) == 0
    assert _count(scoring_db, regime_state) == 0


def test_fresh_spy_runs(scoring_db, monkeypatch):
    _bars(scoring_db, "SPY", ["2026-11-24", "2026-11-25"])
    with pytest.raises(_Reached):
        _score(monkeypatch, "2026-11-25")


def test_no_spy_rows_stops_scoring(scoring_db, monkeypatch):
    _bars(scoring_db, "AAPL", ["2026-11-25"])
    with pytest.raises(SystemExit) as exc:
        _score(monkeypatch, "2026-11-25")
    assert exc.value.code != 0


def test_null_spy_close_on_score_date_stops_scoring(scoring_db, monkeypatch):
    _bars(scoring_db, "SPY", ["2026-11-24"])
    with scoring_db.begin() as conn:
        conn.execute(sa.insert(daily_prices).values(ticker="SPY", date="2026-11-25"))
    with pytest.raises(SystemExit):
        _score(monkeypatch, "2026-11-25")


def test_other_ticker_fresh_does_not_pass_gate(scoring_db, monkeypatch):
    _bars(scoring_db, "SPY", ["2026-11-24"])
    _bars(scoring_db, "QQQ", ["2026-11-25"])
    with pytest.raises(SystemExit):
        _score(monkeypatch, "2026-11-25")


def test_historical_score_date_with_later_bars_runs(scoring_db, monkeypatch):
    _bars(scoring_db, "SPY", ["2026-11-24", "2026-11-25"])
    with pytest.raises(_Reached):
        _score(monkeypatch, "2026-11-24")
