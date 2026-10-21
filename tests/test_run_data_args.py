"""run_data.py flags: --refresh-prices (packet 38) and --rebuild-insider (packet 39, AC10)."""

import pytest

import run_data


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
