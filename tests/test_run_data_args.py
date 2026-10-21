"""run_data.py: --refresh-prices (packet 38), --rebuild-insider (packet 39), shared .info (40),
shared earningsTrend and no .calendar (41)."""

from datetime import timedelta

import pytest
import sqlalchemy as sa

import run_data
from data._utils import utc_now
from data.db import analyst_estimates, earnings_calendar, estimate_trends, short_interest
from data.earnings_calendar import update_earnings_calendar
from tests.yahoo_fake import INFO, PGR_TREND, FakeYahoo


def test_refresh_prices_flag(monkeypatch):
    monkeypatch.setattr("sys.argv", ["run_data.py", "--refresh-prices"])
    assert run_data._parse_args().refresh_prices is True


def test_refresh_prices_off_by_default(monkeypatch):
    monkeypatch.setattr("sys.argv", ["run_data.py", "--no-filings", "--no-13f"])
    assert run_data._parse_args().refresh_prices is False


def _fail(name):
    def stub(*args, **kwargs):
        raise AssertionError(f"{name} must not run with --rebuild-insider")

    return stub


@pytest.fixture
def stubbed_run_data(tmp_path, monkeypatch):
    """run_data.main against a throw-away SQLite file, every ingestion step trapped."""
    monkeypatch.setattr(run_data, "database_url", lambda: f"sqlite:///{tmp_path / 'run.db'}")
    monkeypatch.setattr(run_data, "_setup_logging", lambda config: None)
    monkeypatch.setattr(run_data, "Providers", lambda: object())
    monkeypatch.setattr(run_data, "get_all_tickers", lambda conn, config, force: ["AAPL", "SPY"])
    monkeypatch.setattr(run_data, "fetch_sp500", lambda conn, config: ["AAPL", "MSFT"])
    for name in (
        "update_prices",
        "update_fundamentals",
        "update_short_interest",
        "update_estimates",
        "update_earnings_calendar",
        "update_sec_data",
        "update_transcripts",
        "update_institutional",
    ):
        monkeypatch.setattr(run_data, name, _fail(name))
    calls = []

    def fake_rebuild(conn, tickers, config):
        calls.append(tickers)
        return dict.fromkeys(tickers, 1)

    monkeypatch.setattr(run_data, "rebuild_insider_transactions", fake_rebuild)
    return calls


def test_flag_parsed(monkeypatch):
    monkeypatch.setattr("sys.argv", ["run_data.py", "--rebuild-insider"])
    assert run_data._parse_args().rebuild_insider is True
    monkeypatch.setattr("sys.argv", ["run_data.py"])
    assert run_data._parse_args().rebuild_insider is False


def test_rebuild_runs_only_the_rebuild(stubbed_run_data, monkeypatch):
    monkeypatch.setattr("sys.argv", ["run_data.py", "--rebuild-insider"])
    run_data.main()
    assert stubbed_run_data == [["AAPL", "MSFT"]]


def test_without_flag_rebuild_not_called(stubbed_run_data, monkeypatch):
    monkeypatch.setattr("sys.argv", ["run_data.py"])
    with pytest.raises(AssertionError, match="update_prices"):
        run_data.main()
    assert stubbed_run_data == []


# Adversarial: a scoped rebuild would delete every insider row but re-insert only a few tickers.
def test_rebuild_with_tickers_rejected(stubbed_run_data, monkeypatch):
    monkeypatch.setattr("sys.argv", ["run_data.py", "--rebuild-insider", "--tickers", "AAPL"])
    with pytest.raises(SystemExit):
        run_data.main()
    assert stubbed_run_data == []


# ---------------------------------------------------------------------------
# Packet 40 (AC2, AC6): short interest and estimates share one .info per ticker
# ---------------------------------------------------------------------------


@pytest.fixture
def snapshot_run(tmp_path, monkeypatch):
    """run_data.main with real short interest and estimates steps, Yahoo faked."""
    monkeypatch.setattr(run_data, "database_url", lambda: f"sqlite:///{tmp_path / 'run.db'}")
    monkeypatch.setattr(run_data, "_setup_logging", lambda config: None)
    monkeypatch.setattr(run_data, "Providers", lambda: object())
    monkeypatch.setattr(run_data, "get_all_tickers", lambda conn, config, force: ["AAPL", "SPY"])
    monkeypatch.setattr(run_data, "fetch_sp500", lambda conn, config: ["AAPL", "MSFT"])
    monkeypatch.setattr(run_data, "update_prices", lambda *a, **k: {})
    monkeypatch.setattr(run_data, "update_fundamentals", lambda *a, **k: {})
    monkeypatch.setattr(run_data, "update_earnings_calendar", lambda *a, **k: {})
    monkeypatch.setattr("data.yahoo_info.time.sleep", lambda s: None)
    monkeypatch.setattr("sys.argv", ["run_data.py", "--no-filings", "--no-13f"])

    def install(fake):
        monkeypatch.setattr("yfinance.Ticker", fake)
        return fake

    return install


def test_one_info_fetch_per_ticker(snapshot_run, tmp_path):
    fake = snapshot_run(FakeYahoo(default_info=INFO))
    run_data.main()
    assert fake.calls["info"] == {"AAPL": 1, "MSFT": 1}
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'run.db'}")
    with engine.connect() as conn:
        for table in (short_interest, analyst_estimates):
            assert conn.execute(sa.select(sa.func.count()).select_from(table)).scalar() == 2
    engine.dispose()


def test_low_coverage_run_continues(snapshot_run, caplog):
    failing = {"AAPL": [RuntimeError("429")], "MSFT": [RuntimeError("429")]}
    snapshot_run(FakeYahoo(info=failing, default_trend=PGR_TREND))
    with caplog.at_level("INFO"):
        run_data.main()
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert errors == [
        "Short interest: 0/2 tickers fetched after retries (below 95%)",
        "Analyst estimates: 0/2 tickers fetched after retries (below 95%)",
    ]
    assert "Layer 1 ingestion complete" in caplog.text


# ---------------------------------------------------------------------------
# Packet 41 (AC2): one .info and one earningsTrend per ticker, no .calendar
# ---------------------------------------------------------------------------


def test_two_requests_per_ticker(snapshot_run, tmp_path, monkeypatch):
    monkeypatch.setattr(run_data, "update_earnings_calendar", update_earnings_calendar)
    start = (utc_now() + timedelta(days=10)).replace(hour=12, minute=0, second=0, microsecond=0)
    info = {**INFO, "earningsTimestampStart": int(start.timestamp())}
    fake = snapshot_run(FakeYahoo(default_info=info, default_trend=PGR_TREND))
    run_data.main()
    assert fake.calls["info"] == {"AAPL": 1, "MSFT": 1}
    assert fake.calls["trend"] == {"AAPL": 1, "MSFT": 1}
    assert sum(fake.calls["calendar"].values()) == 0
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'run.db'}")
    with engine.connect() as conn:
        counts = {
            t.name: conn.execute(sa.select(sa.func.count()).select_from(t)).scalar()
            for t in (estimate_trends, earnings_calendar)
        }
        eps = conn.execute(sa.select(earnings_calendar.c.eps_estimate)).scalars().all()
    engine.dispose()
    assert counts == {"estimate_trends": 4, "earnings_calendar": 2}
    assert eps == [4.11543, 4.11543]
