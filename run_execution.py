#!/usr/bin/env python3
"""
Layer 6 — Execution entry point.

Usage examples:
  python run_execution.py --dry-run          # Print orders, no Alpaca calls
  python run_execution.py --execute          # Submit today's approved orders
  python run_execution.py --status           # Show open execution_orders rows
  python run_execution.py --sync             # Reconcile positions with broker
  python run_execution.py --slippage         # Print 30-day slippage stats
  python run_execution.py --cancel-pending   # Cancel all open Alpaca orders
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path

import sqlalchemy as sa
import yaml

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).parent))

import analysis.db  # noqa: F401 — register Layer 3 tables
import execution.db  # noqa: F401 — register Layer 6 tables
import factors.db  # noqa: F401 — register Layer 2 tables
import portfolio.db  # noqa: F401 — register Layer 4 tables
import risk.db  # noqa: F401 — register Layer 5 tables
from data.db import get_engine, initialise_schema


def _load_config(path: str = "config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )


def _load_dotenv() -> None:
    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def _print_summary(results: list[dict]) -> None:
    if not results:
        print("No orders executed.")
        return
    header = f"{'Ticker':<8} {'Action':<6} {'Ordered':>10} {'Filled':>10} {'Status':<12}"
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            f"{r['ticker']:<8} {r['action']:<6} "
            f"{r.get('ordered_shares', 0):>10.1f} "
            f"{r.get('filled_shares', 0):>10.1f} "
            f"{r.get('status', ''):<12}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Meridian Layer 6 — Execution")
    parser.add_argument("--dry-run", action="store_true", help="Log orders; no Alpaca calls")
    parser.add_argument("--execute", action="store_true", help="Submit approved orders")
    parser.add_argument("--status", action="store_true", help="Show open execution_orders")
    parser.add_argument("--sync", action="store_true", help="Reconcile positions")
    parser.add_argument("--slippage", action="store_true", help="Print 30-day slippage stats")
    parser.add_argument("--cancel-pending", action="store_true", help="Cancel all open orders")
    parser.add_argument("--date", default=date.today().isoformat(), metavar="DATE")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    _load_dotenv()
    _setup_logging(args.verbose)
    log = logging.getLogger(__name__)

    config = _load_config()
    cache_dir = Path("cache")
    cache_dir.mkdir(exist_ok=True)

    db_url = os.environ.get("DATABASE_URL", config["database"]["url"])
    engine = get_engine(db_url)
    initialise_schema(engine)

    score_date = args.date

    # -----------------------------------------------------------------------
    # --status: show open orders
    # -----------------------------------------------------------------------
    if args.status:
        from execution.db import execution_orders

        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(execution_orders)
                .where(execution_orders.c.status.in_(["PENDING", "PARTIAL"]))
                .order_by(execution_orders.c.created_at.desc())
            ).fetchall()
        if not rows:
            print("No open execution orders.")
        else:
            print(
                f"{'ID':<6} {'Date':<12} {'Ticker':<8} {'Action':<6} {'Ordered':>10} {'Filled':>10} {'Status':<10}"
            )
            for r in rows:
                print(
                    f"{r.id:<6} {r.rebalance_date:<12} {r.ticker:<8} {r.action:<6} "
                    f"{r.ordered_shares:>10.1f} {r.filled_shares:>10.1f} {r.status:<10}"
                )
        return 0

    # -----------------------------------------------------------------------
    # --slippage: 30-day stats
    # -----------------------------------------------------------------------
    if args.slippage:
        from execution.costs import slippage_stats

        with engine.connect() as conn:
            stats = slippage_stats(conn)
        print(f"30-day slippage stats ({stats['count']} filled orders):")
        print(f"  Mean:         {stats['mean_bps']:.1f} bps")
        print(f"  P95:          {stats['p95_bps']:.1f} bps")
        print(f"  Worst ticker: {stats['worst_ticker']}")
        return 0

    # -----------------------------------------------------------------------
    # All other modes need Alpaca client
    # -----------------------------------------------------------------------
    try:
        from execution.broker import get_account, get_client, market_is_open, reconcile_positions

        client = get_client()
        acct = get_account(client)
        log.info(
            "Connected to Alpaca. Equity=%.2f BuyingPower=%.2f",
            acct["equity"],
            acct["buying_power"],
        )
    except OSError as exc:
        log.error("%s", exc)
        return 1

    # -----------------------------------------------------------------------
    # --cancel-pending
    # -----------------------------------------------------------------------
    if args.cancel_pending:
        from execution.order_manager import cancel_open_orders

        n = cancel_open_orders(client)
        print(f"Cancelled {n} open orders.")
        return 0

    # -----------------------------------------------------------------------
    # --sync: reconcile positions
    # -----------------------------------------------------------------------
    if args.sync:
        with engine.connect() as conn:
            corrections = reconcile_positions(conn, client, cache_dir, dry_run=False)
        if not corrections:
            print("Positions are in sync — no corrections needed.")
        else:
            print(f"{len(corrections)} position(s) corrected:")
            for c in corrections:
                print(f"  {c['ticker']}: broker={c['broker_qty']:.2f}  db={c['db_qty']:.2f}")
        return 0

    # -----------------------------------------------------------------------
    # --dry-run or --execute
    # -----------------------------------------------------------------------
    if not args.dry_run and not args.execute:
        parser.print_help()
        return 0

    dry_run = args.dry_run

    with engine.connect() as conn:
        # 1. Reconcile positions (dry-run: log only, no DB writes)
        corrections = reconcile_positions(conn, client, cache_dir, dry_run=dry_run)
        if corrections:
            verb = "would be corrected" if dry_run else "auto-corrected"
            log.warning("%d position(s) %s before execution.", len(corrections), verb)

        # 2. Market open check (warn but don't block)
        market_is_open(client)

        # 3. Execute
        from execution.executor import execute_approvals
        from execution.order_manager import OrderManager

        with OrderManager(client):
            results = execute_approvals(
                conn, client, score_date, config, cache_dir, dry_run=dry_run
            )

    print(f"\nExecution summary for {score_date}:")
    _print_summary(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
