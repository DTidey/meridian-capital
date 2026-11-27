#!/usr/bin/env python3
"""
Meridian Capital Partners — Full pipeline runner (Layers 1–7)

Runs each layer in sequence and stops on the first failure.

Usage:
    python run_all.py                        # full run, all layers
    python run_all.py --no-filings --no-13f  # skip SEC + 13-F (fast daily)
    python run_all.py --no-execution         # skip Alpaca order submission
    python run_all.py --no-reporting         # skip Layer 7
    python run_all.py --dry-run              # pass --dry-run to execution layer
    python run_all.py --tickers AAPL MSFT    # scope ingestion + scoring to specific tickers
    python run_all.py --whatif               # preview only: no commits in portfolio/risk/execution
    python run_all.py --stress               # include stress tests in risk check
    python run_all.py --forms 4              # Layer 1 fetches only these SEC form types
    python run_all.py --ignore-market-calendar  # run even if the market is closed

On a day the US market is closed (weekend or holiday, per Alpaca's calendar) for the score
date, the run is skipped with exit 0 before Layer 1. A failed calendar lookup never skips.
"""

import argparse
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import yaml
from dotenv import load_dotenv

_ROOT = Path(__file__).parent
load_dotenv(_ROOT / ".env")
sys.path.insert(0, str(_ROOT))

from execution.market_calendar import closed_reason  # noqa: E402
from factors.score_date import resolve_score_date  # noqa: E402

_PYTHON = sys.executable

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _run(label: str, cmd: list[str], skip: bool = False) -> bool:
    if skip:
        print(f"\n{YELLOW}  ↷  Skipping {label}{RESET}")
        return True

    print(f"\n{BOLD}{'─' * 60}{RESET}")
    print(f"{BOLD}  ▶  {label}{RESET}")
    print(f"  {' '.join(cmd)}")
    print(f"{BOLD}{'─' * 60}{RESET}")

    t0 = time.time()
    result = subprocess.run(cmd, cwd=_ROOT)
    elapsed = time.time() - t0

    if result.returncode == 0:
        print(f"{GREEN}  ✓  {label} completed in {elapsed:.0f}s{RESET}")
        return True
    else:
        print(f"{RED}  ✗  {label} FAILED (exit {result.returncode}) after {elapsed:.0f}s{RESET}")
        return False


def _load_config() -> dict:
    with open(_ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def _market_closed(ignore_calendar: bool) -> bool:
    """True when the market is closed on Layer 2's score date; any lookup error runs."""
    if ignore_calendar:
        print("Market calendar check skipped (--ignore-market-calendar): running the pipeline")
        return False
    score_date = "unknown date"
    try:
        score_date = resolve_score_date(None, _load_config().get("scoring") or {})
        reason = closed_reason(date.fromisoformat(score_date))
    except Exception as exc:  # fail open: a lookup error must never skip a trading day
        print(
            f"{YELLOW}WARNING: market calendar check failed for {score_date} ({exc}): "
            f"running the pipeline{RESET}",
            flush=True,
        )
        return False
    if reason:
        print(f"Market closed on {score_date} ({reason}): skipping the nightly run")
        return True
    print(f"Market open on {score_date}: running the pipeline", flush=True)
    return False


def main() -> None:
    p = argparse.ArgumentParser(description="Meridian Capital Partners — full pipeline")
    p.add_argument("--no-filings", action="store_true", help="Skip SEC filings (Layer 1)")
    p.add_argument("--no-13f", action="store_true", help="Skip 13-F holdings (Layer 1)")
    p.add_argument("--tickers", nargs="+", help="Scope to specific tickers")
    p.add_argument("--no-execution", action="store_true", help="Skip Layer 6 (Alpaca)")
    p.add_argument("--no-reporting", action="store_true", help="Skip Layer 7 (reporting)")
    p.add_argument("--dry-run", action="store_true", help="Dry-run execution layer")
    p.add_argument("--whatif", action="store_true", help="Preview mode: no commits")
    p.add_argument("--stress", action="store_true", help="Include stress tests in risk check")
    p.add_argument(
        "--forms", nargs="+", metavar="FORM", help="SEC form types for Layer 1, e.g. --forms 4"
    )
    p.add_argument(
        "--ignore-market-calendar",
        action="store_true",
        help="Run even if the market is closed on the score date",
    )
    args = p.parse_args()

    t_start = time.time()
    print(f"\n{BOLD}{'═' * 60}")
    print("  MERIDIAN CAPITAL PARTNERS — Pipeline Runner")
    print(f"{'═' * 60}{RESET}")

    if _market_closed(args.ignore_market_calendar):
        return

    failures: list[str] = []

    def run(label, cmd, skip=False):
        ok = _run(label, cmd, skip=skip)
        if not ok:
            failures.append(label)
        return ok

    # ------------------------------------------------------------------
    # Layer 1 — Data ingestion
    # ------------------------------------------------------------------
    l1_cmd = [_PYTHON, "run_data.py"]
    if args.no_filings:
        l1_cmd.append("--no-filings")
    if args.no_13f:
        l1_cmd.append("--no-13f")
    if args.forms:
        l1_cmd += ["--forms"] + args.forms
    if args.tickers:
        l1_cmd += ["--tickers"] + args.tickers
    if not run("Layer 1 — Data ingestion", l1_cmd):
        _summary(failures, t_start)
        return

    # ------------------------------------------------------------------
    # Layer 2 — Factor scoring
    # ------------------------------------------------------------------
    l2_cmd = [_PYTHON, "run_scoring.py"]
    if args.tickers:
        l2_cmd += ["--ticker"] + args.tickers
    if not run("Layer 2 — Factor scoring", l2_cmd):
        _summary(failures, t_start)
        return

    # ------------------------------------------------------------------
    # Layer 3 — AI analysis
    # ------------------------------------------------------------------
    l3_cmd = [_PYTHON, "run_analysis.py"]
    if args.tickers:
        l3_cmd += ["--ticker", args.tickers[0]]
    if not run("Layer 3 — AI analysis", l3_cmd):
        _summary(failures, t_start)
        return

    # ------------------------------------------------------------------
    # Book sync — positions from Alpaca, the book of record (also in --whatif)
    # ------------------------------------------------------------------
    sync_cmd = [_PYTHON, "run_execution.py", "--sync"]
    synced = run("Book sync", sync_cmd, skip=args.no_execution)
    if not synced and not args.whatif:
        _summary(failures, t_start)  # never rebalance against a stale book
        return

    # ------------------------------------------------------------------
    # Layer 4 — Portfolio construction
    # ------------------------------------------------------------------
    l4_cmd = [_PYTHON, "run_portfolio.py"]
    if args.whatif:
        l4_cmd.append("--whatif")
    else:
        l4_cmd.append("--rebalance")
    if not run("Layer 4 — Portfolio construction", l4_cmd):
        _summary(failures, t_start)
        return

    # ------------------------------------------------------------------
    # Layer 5 — Risk check
    # ------------------------------------------------------------------
    l5_cmd = [_PYTHON, "run_risk_check.py"]
    if args.whatif:
        l5_cmd.append("--whatif")
    if args.stress:
        l5_cmd.append("--stress")
    if not run("Layer 5 — Risk management", l5_cmd):
        _summary(failures, t_start)
        return

    # ------------------------------------------------------------------
    # Layer 6 — Execution
    # ------------------------------------------------------------------
    l6_cmd = [_PYTHON, "run_execution.py", "--execute"]
    if args.dry_run:
        l6_cmd.append("--dry-run")
    if args.whatif:
        l6_cmd = [_PYTHON, "run_execution.py", "--status"]
    run("Layer 6 — Execution", l6_cmd, skip=args.no_execution)

    # ------------------------------------------------------------------
    # Layer 7 — Reporting
    # ------------------------------------------------------------------
    l7_cmd = [_PYTHON, "run_reporting.py", "--all"]
    run("Layer 7 — Reporting", l7_cmd, skip=args.no_reporting)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    _summary(failures, t_start)


def _summary(failures: list[str], t_start: float) -> None:
    elapsed = time.time() - t_start
    print(f"\n{BOLD}{'═' * 60}{RESET}")
    if not failures:
        print(f"{GREEN}{BOLD}  ✓  All layers completed successfully in {elapsed:.0f}s{RESET}")
    else:
        print(f"{RED}{BOLD}  ✗  Pipeline failed at: {', '.join(failures)}{RESET}")
        print(f"{RED}     Total time: {elapsed:.0f}s{RESET}")
    print(f"{BOLD}{'═' * 60}{RESET}\n")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
