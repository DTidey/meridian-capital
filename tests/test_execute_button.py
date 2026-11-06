"""Packet 62: research page "Execute All Approved Trades" button (AC1-AC6)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa

import dashboard.page_research as page
from dashboard import execute_approvals as ex
from execution.db import execution_orders
from portfolio.db import position_approvals

_ROOT = Path(__file__).parent.parent
_D = "2026-11-05"
_OLD = "2026-05-21"


def _approval(engine, ticker, date=_D, status="PENDING", action="BUY"):
    with engine.begin() as conn:
        conn.execute(
            position_approvals.insert().values(
                rebalance_date=date,
                ticker=ticker,
                action=action,
                target_shares=10.0,
                current_shares=0.0,
                delta_shares=10.0,
                status=status,
                created_at=f"{date}T22:00:00",
            )
        )


def _order(conn, ticker, status="PENDING", order_id="id-1", action="BUY", created="9999"):
    conn.execute(
        execution_orders.insert().values(
            rebalance_date=_D,
            ticker=ticker,
            action=action,
            ordered_shares=10.0,
            filled_shares=0.0,
            status=status,
            order_id=order_id,
            created_at=created,
            updated_at=created,
        )
    )


def _statuses(engine):
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(
                position_approvals.c.rebalance_date,
                position_approvals.c.ticker,
                position_approvals.c.status,
            )
        ).fetchall()
    return {(d, t): s for d, t, s in rows}


# ---------------------------------------------------------------------------
# Page harness: streamlit and subprocess mocked, the click always lands
# ---------------------------------------------------------------------------


@pytest.fixture
def ui(monkeypatch, tmp_engine):
    st = MagicMock()
    st.button.return_value = True
    monkeypatch.setattr(page, "st", st)
    monkeypatch.setattr(page, "_run_veto_checks", lambda engine, cfg, pending: {})
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return fake_run.effect(cmd, kwargs)

    fake_run.effect = lambda cmd, kwargs: subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(ex.subprocess, "run", fake_run)
    return MagicMock(st=st, run=fake_run, calls=calls, engine=tmp_engine)


def _submitting(engine, tickers, status="PENDING", rc=0, stderr=""):
    """A fake Layer 6 that records one order per ticker, then exits with rc."""

    def effect(cmd, kwargs):
        with engine.begin() as conn:
            for t in tickers:
                _order(conn, t, status=status, order_id=None if status == "FAILED" else f"o-{t}")
        return subprocess.CompletedProcess(cmd, rc, "Execution summary", stderr)

    return effect


def _messages(st, level):
    return [c.args[0] for c in getattr(st, level).call_args_list]


# AC1 ------------------------------------------------------------------------


def test_command_is_execute_with_date():
    cmd = ex.execution_command(_D)
    assert cmd[1:] == [str(_ROOT / "run_execution.py"), "--execute", "--date", _D]


def test_command_refuses_non_iso_date():
    with pytest.raises(ValueError):
        ex.execution_command("2026-11-05; rm -rf /")


def test_click_runs_execute_for_latest_rebalance_date(ui):
    _approval(ui.engine, "AAPL", date="2026-11-04", status="APPROVED")
    _approval(ui.engine, "MSFT")
    page._approval_section(ui.engine, {})
    [(cmd, kwargs)] = ui.calls
    assert cmd[-3:] == ["--execute", "--date", _D]
    assert kwargs["cwd"] == _ROOT
    assert kwargs["capture_output"] is True


# AC2 ------------------------------------------------------------------------


def test_stale_pending_rows_never_approved_or_run(ui):
    # Adversarial: the live DB holds 8 PENDING rows from 2026-05-21.
    _approval(ui.engine, "OLD", date=_OLD)
    _approval(ui.engine, "NEW")
    ui.run.effect = _submitting(ui.engine, ["NEW"])
    page._approval_section(ui.engine, {})
    assert _statuses(ui.engine) == {(_OLD, "OLD"): "PENDING", (_D, "NEW"): "APPROVED"}
    assert any("1 PENDING row(s) from older rebalances" in m for m in _messages(ui.st, "info"))


def test_only_stale_pending_shows_no_button(ui):
    _approval(ui.engine, "OLD", date=_OLD)
    _approval(ui.engine, "NEWA", status="APPROVED")
    page._approval_section(ui.engine, {})
    ui.st.button.assert_not_called()
    assert ui.calls == []
    assert _statuses(ui.engine)[(_OLD, "OLD")] == "PENDING"


def test_trimmed_and_rejected_rows_untouched(ui):
    _approval(ui.engine, "TRIM", status="TRIMMED")
    _approval(ui.engine, "REJ", status="REJECTED")
    _approval(ui.engine, "PEND")
    ui.run.effect = _submitting(ui.engine, ["PEND"])
    page._approval_section(ui.engine, {})
    s = _statuses(ui.engine)
    assert (s[(_D, "TRIM")], s[(_D, "REJ")], s[(_D, "PEND")]) == ("TRIMMED", "REJECTED", "APPROVED")


def test_vetoed_rows_stay_pending(ui, monkeypatch):
    monkeypatch.setattr(page, "_run_veto_checks", lambda e, c, p: {"BAD": "Earnings blackout"})
    _approval(ui.engine, "BAD")
    _approval(ui.engine, "GOOD")
    ui.run.effect = _submitting(ui.engine, ["GOOD"])
    page._approval_section(ui.engine, {})
    s = _statuses(ui.engine)
    assert (s[(_D, "BAD")], s[(_D, "GOOD")]) == ("PENDING", "APPROVED")


# AC3 / AC6 ------------------------------------------------------------------


def test_success_only_when_every_approved_ticker_submitted(ui):
    _approval(ui.engine, "AAPL")
    _approval(ui.engine, "MSFT")
    ui.run.effect = _submitting(ui.engine, ["AAPL", "MSFT"])
    page._approval_section(ui.engine, {})
    assert _messages(ui.st, "success") == ["Submitted 2 order(s) to Alpaca."]
    ui.st.error.assert_not_called()
    table = ui.st.dataframe.call_args.args[0]
    assert sorted(table["ticker"]) == ["AAPL", "MSFT"]
    assert list(table.columns) == ["ticker", "action", "ordered_shares", "status", "order_id"]


def test_orders_before_click_not_counted(tmp_engine):
    with tmp_engine.begin() as conn:
        _order(conn, "AAPL", created="2026-11-05T22:00:00+00:00")
        _order(conn, "MSFT", created="2026-11-06T15:00:00+00:00")
        rows = ex.orders_since(conn, _D, "2026-11-06T14:59:00+00:00")
    assert [r["ticker"] for r in rows] == ["MSFT"]


# AC4 ------------------------------------------------------------------------


def test_nonzero_exit_is_error_with_stderr(ui):
    # Adversarial: the script exits non-zero.
    _approval(ui.engine, "AAPL")
    ui.run.effect = lambda cmd, kw: subprocess.CompletedProcess(cmd, 2, "", "Traceback: boom")
    page._approval_section(ui.engine, {})
    ui.st.success.assert_not_called()
    [msg] = _messages(ui.st, "error")
    assert "exit 2" in msg
    assert "Traceback: boom" in ui.st.code.call_args_list[-1].args[0]


def test_no_close_for_date_is_error(ui):
    # Adversarial: no close stored for D; Layer 6 exits 1 (packet 60) and records nothing.
    _approval(ui.engine, "AAPL")
    ui.run.effect = lambda cmd, kw: subprocess.CompletedProcess(
        cmd, 1, "", "ERROR No close stored for 2026-11-05: no orders placed"
    )
    page._approval_section(ui.engine, {})
    ui.st.success.assert_not_called()
    assert "exit 1" in _messages(ui.st, "error")[0]


def test_failure_after_some_orders_lists_them(ui):
    _approval(ui.engine, "AAPL")
    ui.run.effect = _submitting(ui.engine, ["AAPL"], rc=1)
    page._approval_section(ui.engine, {})
    ui.st.success.assert_not_called()
    assert "1 order(s) were recorded" in _messages(ui.st, "error")[0]
    ui.st.dataframe.assert_called_once()


# AC5 ------------------------------------------------------------------------


def test_exit_zero_with_no_orders_is_warning(ui):
    # Adversarial: the original bug (help text, exit 0) and any run that submits nothing.
    _approval(ui.engine, "AAPL")
    page._approval_section(ui.engine, {})
    ui.st.success.assert_not_called()
    assert _messages(ui.st, "warning")[-1].startswith("No orders were submitted")


def test_second_click_while_orders_open_is_not_success(ui):
    # Adversarial: orders from an earlier click are still open; Layer 6 skips them
    # (already_submitted / open_order) and records nothing new.
    with ui.engine.begin() as conn:
        _order(conn, "AAPL", created="2000-01-01T00:00:00+00:00")
    _approval(ui.engine, "AAPL")
    page._approval_section(ui.engine, {})
    ui.st.success.assert_not_called()
    assert _messages(ui.st, "warning")[-1].startswith("No orders were submitted")


def test_partial_submission_names_failed_and_missing(ui):
    for t in ("AAPL", "MSFT", "NVDA"):
        _approval(ui.engine, t)

    def effect(cmd, kw):
        with ui.engine.begin() as conn:
            _order(conn, "AAPL", order_id="o-1")
            _order(conn, "MSFT", status="FAILED", order_id=None)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    ui.run.effect = effect
    page._approval_section(ui.engine, {})
    ui.st.success.assert_not_called()
    msg = _messages(ui.st, "warning")[-1]
    assert "Submitted 1 order(s)" in msg
    assert "failed: MSFT" in msg
    assert "not submitted: NVDA" in msg


# outcome() unit cases --------------------------------------------------------


@pytest.mark.parametrize(
    "rc, orders, level",
    [
        (0, [], "warning"),
        (0, [{"ticker": "A", "status": "PENDING", "order_id": "x"}], "success"),
        (0, [{"ticker": "A", "status": "FILLED", "order_id": "x"}], "success"),
        (0, [{"ticker": "A", "status": "DRY_RUN", "order_id": "dry-run"}], "warning"),
        (0, [{"ticker": "A", "status": "PENDING", "order_id": None}], "warning"),
        (1, [{"ticker": "A", "status": "PENDING", "order_id": "x"}], "error"),
    ],
)
def test_outcome_levels(rc, orders, level):
    assert ex.outcome(rc, orders, ["A"]).level == level


# AC10 -----------------------------------------------------------------------


def test_veto_keys_are_tickers(tmp_engine, tmp_path, monkeypatch):
    # Rows were indexed row[1] (rebalance_date): every veto keyed on the date and named it.
    _approval(tmp_engine, "AAPL")
    _approval(tmp_engine, "MSFT", action="SHORT")
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "halt.lock").write_text("x")
    monkeypatch.chdir(tmp_path)
    with tmp_engine.connect() as conn:
        pending = ex.pending_rows(conn, _D)
    assert set(page._run_veto_checks(tmp_engine, {}, pending)) == {"AAPL", "MSFT"}
