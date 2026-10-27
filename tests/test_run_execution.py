"""Packet 49 (AC5): run_execution syncs orders before the book and before executing."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

import execution.book_sync
import execution.broker
import execution.executor
import execution.order_sync
import run_execution


@pytest.fixture
def run(monkeypatch, tmp_path):
    """Fakes for every step; .calls records their order, .results feeds execute_approvals."""
    state = SimpleNamespace(calls=[], results=[])
    log = state.calls
    client = MagicMock()
    client.get_clock.return_value = SimpleNamespace(is_open=False, next_open="tomorrow")
    monkeypatch.setattr(run_execution, "database_url", lambda: f"sqlite:///{tmp_path / 'e.db'}")
    monkeypatch.setattr(execution.broker, "get_client", lambda: client)
    monkeypatch.setattr(
        execution.broker,
        "get_account",
        lambda c: {"equity": 1.0, "buying_power": 1.0, "status": "x"},
    )

    def sync_orders(conn, c):
        log.append("orders")
        return {"checked": 0, "updated": 0, "final": 0}

    def sync_book(conn, c, date, dry_run=False):
        log.append("book-dry" if dry_run else "book")
        return {"positions": 0, "added": [], "removed": [], "changed": []}

    def execute(conn, c, date, config, cache_dir, dry_run=False):
        log.append("execute")
        return state.results

    monkeypatch.setattr(execution.order_sync, "sync_orders", sync_orders)
    monkeypatch.setattr(execution.book_sync, "sync_book", sync_book)
    monkeypatch.setattr(execution.executor, "execute_approvals", execute)
    monkeypatch.chdir(Path(__file__).parent.parent)
    return state


def _main(monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["run_execution.py", *argv])
    return run_execution.main()


def test_sync_orders_before_book(monkeypatch, run):
    assert _main(monkeypatch, "--sync") == 0
    assert run.calls == ["orders", "book"]


def test_execute_syncs_orders_first(monkeypatch, run):
    assert _main(monkeypatch, "--execute") == 0
    assert run.calls == ["orders", "book", "execute", "book"]


def test_dry_run_syncs_orders_and_dry_book(monkeypatch, run):
    assert _main(monkeypatch, "--dry-run") == 0
    assert run.calls == ["orders", "book-dry", "execute"]


def test_all_no_close_fails_the_run(monkeypatch, run):
    # Adversarial: a wrong --date (e.g. past UK midnight) must not pass silently.
    run.results = [{"ticker": "AAPL", "action": "BUY", "status": "SKIPPED", "reason": "no_close"}]
    assert _main(monkeypatch, "--execute") == 1


def test_some_no_close_still_succeeds(monkeypatch, run):
    run.results = [
        {"ticker": "AAPL", "action": "BUY", "status": "SKIPPED", "reason": "no_close"},
        {"ticker": "MSFT", "action": "BUY", "status": "SUBMITTED"},
    ]
    assert _main(monkeypatch, "--execute") == 0
