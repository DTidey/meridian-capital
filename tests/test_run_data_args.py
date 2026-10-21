"""run_data.py command-line flags (packet 38)."""

import run_data


def test_refresh_prices_flag(monkeypatch):
    monkeypatch.setattr("sys.argv", ["run_data.py", "--refresh-prices"])
    assert run_data._parse_args().refresh_prices is True


def test_refresh_prices_off_by_default(monkeypatch):
    monkeypatch.setattr("sys.argv", ["run_data.py", "--no-filings", "--no-13f"])
    assert run_data._parse_args().refresh_prices is False
