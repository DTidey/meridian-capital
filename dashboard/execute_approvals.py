"""Research page bulk execute: approve PENDING rows for the latest rebalance, run Layer 6, report.

Packet 62. The page reports what Layer 6 did from its exit code and the execution_orders rows
it wrote, never from the number of rows it approved.
"""

from __future__ import annotations

import subprocess  # nosec B404
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import sqlalchemy as sa

from execution.db import execution_orders
from portfolio.db import position_approvals

_ROOT = Path(__file__).parent.parent
_NOT_SUBMITTED = ("FAILED", "DRY_RUN")
ORDER_COLUMNS = ["ticker", "action", "ordered_shares", "status", "order_id"]


@dataclass
class Outcome:
    level: str  # "success" | "warning" | "error"
    message: str
    submitted: list[str] = field(default_factory=list)


def latest_rebalance_date(conn) -> str | None:
    return conn.execute(sa.select(sa.func.max(position_approvals.c.rebalance_date))).scalar()


def pending_rows(conn, rebalance_date: str) -> list:
    return conn.execute(
        sa.select(position_approvals).where(
            (position_approvals.c.rebalance_date == rebalance_date)
            & (position_approvals.c.status == "PENDING")
        )
    ).fetchall()


def stale_pending_count(conn, rebalance_date: str) -> int:
    """PENDING rows from older rebalances: never executable (their close is stale)."""
    return conn.execute(
        sa.select(sa.func.count()).where(
            (position_approvals.c.rebalance_date < rebalance_date)
            & (position_approvals.c.status == "PENDING")
        )
    ).scalar()


def approve_pending(conn, ids: list[int]) -> None:
    conn.execute(
        position_approvals.update()
        .where(position_approvals.c.id.in_(ids) & (position_approvals.c.status == "PENDING"))
        .values(status="APPROVED")
    )


def execution_command(rebalance_date: str) -> list[str]:
    date.fromisoformat(rebalance_date)  # ValueError on anything but YYYY-MM-DD
    return [sys.executable, str(_ROOT / "run_execution.py"), "--execute", "--date", rebalance_date]


def run_execution(rebalance_date: str) -> subprocess.CompletedProcess:
    # Fixed argv, validated date, no shell.
    return subprocess.run(  # nosec B603
        execution_command(rebalance_date), cwd=_ROOT, capture_output=True, text=True
    )


def orders_since(conn, rebalance_date: str, since: str) -> list[dict]:
    rows = conn.execute(
        sa.select(*[execution_orders.c[c] for c in ORDER_COLUMNS])
        .where(
            (execution_orders.c.rebalance_date == rebalance_date)
            & (execution_orders.c.created_at >= since)
        )
        .order_by(execution_orders.c.id)
    ).fetchall()
    return [dict(r._mapping) for r in rows]


def _is_submitted(order: dict) -> bool:
    return order["order_id"] is not None and order["status"] not in _NOT_SUBMITTED


def outcome(returncode: int, orders: list[dict], approved: list[str]) -> Outcome:
    """Summarise a Layer 6 run: success only on exit 0 with every approved ticker submitted."""
    sent = [o for o in orders if _is_submitted(o)]
    tickers = [o["ticker"] for o in sent]
    if returncode != 0:
        msg = f"Execution failed (exit {returncode}). {len(sent)} order(s) were recorded."
        return Outcome("error", msg, tickers)
    if not sent:
        return Outcome("warning", "No orders were submitted. See the log below.", tickers)
    failed = sorted({o["ticker"] for o in orders if not _is_submitted(o)})
    missing = sorted(set(approved) - {o["ticker"] for o in orders})
    msg = f"Submitted {len(sent)} order(s)"
    if not failed and not missing:
        return Outcome("success", f"{msg} to Alpaca.", tickers)
    if failed:
        msg += f"; failed: {', '.join(failed)}"
    if missing:
        msg += f"; not submitted: {', '.join(missing)}"
    return Outcome("warning", msg + ". See the log below.", tickers)
