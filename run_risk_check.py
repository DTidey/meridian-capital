#!/usr/bin/env python3
"""
Meridian Capital Partners — Layer 5: Risk Management

Usage:
    python run_risk_check.py
    python run_risk_check.py --stress
    python run_risk_check.py --tail-only
    python run_risk_check.py --pre-trade-only
    python run_risk_check.py --clear-halt
    python run_risk_check.py --whatif
    python run_risk_check.py --date YYYY-MM-DD
"""

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import sqlalchemy as sa
import yaml
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).parent
load_dotenv(_ROOT / ".env")
sys.path.insert(0, str(_ROOT))

import risk.db        # noqa: F401, E402  — registers tables
import portfolio.db   # noqa: F401, E402
import analysis.db    # noqa: F401, E402
import factors.db     # noqa: F401, E402

from data.db import get_engine, initialise_schema           # noqa: E402
from portfolio.db import portfolio_positions as pos_table    # noqa: E402
from portfolio.state import load_positions                   # noqa: E402
from portfolio.beta import compute_betas, portfolio_beta     # noqa: E402
from risk.risk_state import (                                # noqa: E402
    load_risk_state, save_risk_state, is_halted, clear_halt,
)
from risk.factor_risk_model import compute_factor_risk, save_predicted_cov   # noqa: E402
from risk.pre_trade import run_pre_trade                     # noqa: E402
from risk.circuit_breakers import run_circuit_breakers       # noqa: E402
from risk.factor_monitor import run_factor_monitor           # noqa: E402
from risk.correlation_monitor import run_correlation_monitor # noqa: E402
from risk.tail_risk import run_tail_risk                     # noqa: E402
from risk.stress_test import run_stress_tests                # noqa: E402

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )


def _resolve_score_date(config: dict, cli_date: str | None) -> str:
    if cli_date:
        return cli_date
    cfg = config.get("risk", {}).get("score_date")
    if cfg:
        return str(cfg)
    return str(date.today())


def _resolve_cache_dir(config: dict) -> Path:
    cache_dir = _ROOT / "cache"
    cache_dir.mkdir(exist_ok=True)
    return cache_dir


# ---------------------------------------------------------------------------
# Print helpers
# ---------------------------------------------------------------------------

def _build_mctr_top5(frm, positions: pd.DataFrame) -> list[dict]:
    if frm.mctr.empty or positions.empty:
        return []
    mctr_abs = frm.mctr.abs().sort_values(ascending=False)
    top5 = mctr_abs.head(5).index.tolist()
    weight_map = positions.set_index("ticker")["weight"].to_dict()
    return [
        {
            "ticker": t,
            "weight": round(weight_map.get(t, 0.0), 4),
            "mctr":   round(float(frm.mctr.get(t, 0.0)), 4),
        }
        for t in top5
    ]


def _print_factor_risk(frm) -> None:
    top_factor = max(frm.factor_contributions, key=frm.factor_contributions.get) \
        if frm.factor_contributions else "—"
    top_pct = frm.factor_contributions.get(top_factor, 0.0) * 100

    print(f"  Annualised vol   : {frm.total_vol * 100:>5.1f}%")
    print(f"  Factor risk      : {frm.factor_var_pct * 100:>5.0f}%  (of total variance)")
    print(f"  Specific risk    : {frm.specific_var_pct * 100:>5.0f}%")
    print(f"  Top factor       :  {top_factor}  {top_pct:.1f}%")

    if frm.mctr_flags:
        print()
        print("  MCTR concentrations (flagged):")
        for ticker in frm.mctr_flags:
            mctr_v  = float(frm.mctr.get(ticker, 0.0))
            mctr_pct = abs(mctr_v) / frm.total_vol * 100 if frm.total_vol else 0.0
            print(f"    {ticker:<6} MCTR  {mctr_pct:.1f}%  ⚠")


def _print_pre_trade(df: pd.DataFrame) -> None:
    if df.empty:
        print("  No pending trades.")
        return

    total = len(df)
    print(f"Pre-Trade Veto ({total} pending trades):")

    counts = df["result"].value_counts()
    approved = counts.get("APPROVED", 0)
    rejected = counts.get("REJECTED", 0)
    reduced  = counts.get("REDUCED",  0)

    rej_detail = ""
    if rejected:
        rej_rows = df[df["result"] == "REJECTED"]
        rej_detail = "  (" + ", ".join(
            f"{r['ticker']} — {r.get('reason', '?')}"
            for _, r in rej_rows.iterrows()
        ) + ")"

    red_detail = ""
    if reduced:
        red_rows = df[df["result"] == "REDUCED"]
        red_detail = "  (" + ", ".join(
            f"{r['ticker']} — {r.get('reason', '?')}"
            for _, r in red_rows.iterrows()
        ) + ")"

    print(f"  APPROVED : {approved}")
    print(f"  REJECTED : {rejected}{rej_detail}")
    print(f"  REDUCED  : {reduced}{red_detail}")


def _print_circuit_breakers(risk_state: dict) -> None:
    state         = risk_state.get("circuit_breaker_state", "UNKNOWN")
    daily_pnl     = risk_state.get("daily_pnl_pct", 0.0)
    weekly_pnl    = risk_state.get("weekly_pnl_pct", 0.0)
    drawdown      = risk_state.get("drawdown_pct", 0.0)

    print(f"Circuit Breakers: {state}")
    print(f"  Daily P&L    : {daily_pnl:>+7.2%}")
    print(f"  Weekly P&L   : {weekly_pnl:>+7.2%}")
    print(f"  Drawdown     : {drawdown:>7.1%}")


def _print_tail_risk(result: dict) -> None:
    state    = result.get("tail_risk_state", "UNKNOWN")
    vix      = result.get("vix", float("nan"))
    cs_z     = result.get("credit_spread_z", float("nan"))
    actions  = result.get("actions", [])

    print(f"  VIX              : {vix:.1f}")
    print(f"  Credit spread z  : {cs_z:+.2f}")
    print(f"  State            : {state}")
    for action in actions:
        print(f"  Action           : {action}")


def _print_factor_monitor(alerts: list[dict]) -> None:
    if not alerts:
        print("  No factor alerts.")
        return
    for alert in alerts:
        factor   = alert.get("factor", alert.get("type", "?"))
        z        = alert.get("z", float("nan"))
        priority = alert.get("priority", "")
        print(f"  [{priority:<6}] {factor:<30}  z = {z:+.2f}")


def _print_correlation(result: dict) -> None:
    long_corr  = result.get("long_avg_corr",  float("nan"))
    short_corr = result.get("short_avg_corr", float("nan"))
    eff_n      = result.get("effective_n_bets", float("nan"))
    alerts     = result.get("alerts", [])

    print(f"  Long avg corr    : {long_corr:.3f}")
    print(f"  Short avg corr   : {short_corr:.3f}")
    print(f"  Effective N bets : {eff_n:.1f}")
    for alert in alerts:
        print(f"  ⚠  {alert.get('message', alert)}")


def _print_alerts(alerts: list[dict]) -> None:
    if not alerts:
        print("  No alerts.")
        return

    priority_order = {"HIGH": 0, "MEDIUM": 1, "MED": 1, "LOW": 2}
    sorted_alerts  = sorted(alerts, key=lambda a: priority_order.get(a.get("priority", "LOW"), 2))

    for alert in sorted_alerts:
        priority = alert.get("priority", "LOW")
        tag      = {"HIGH": "HIGH", "MEDIUM": "MED", "MED": "MED"}.get(priority, "LOW")
        message  = alert.get("message", str(alert))
        print(f"  [{tag:<4}] {message}")


def _print_stress(results: list, nav_usd: float) -> None:
    print(f"  {'Scenario':<35} {'Total P&L':>12}  {'Long':>7}  {'Short':>9}  "
          f"{'Worst Long':<10}  {'Worst Short'}")
    print("  " + "-" * 100)
    for r in results:
        total_pct = r.total_pnl_pct * 100
        long_pct  = (r.long_pnl_usd / nav_usd * 100) if nav_usd else 0.0
        short_k   = f"${r.short_pnl_usd / 1_000:+.0f}K" if abs(r.short_pnl_usd) >= 1_000 else f"${r.short_pnl_usd:+.0f}"
        total_str = f"${r.total_pnl_usd:>+,.0f}"
        print(f"  {r.name:<35} {total_str:>12}  {long_pct:>+6.1f}%  {short_k:>9}  "
              f"{r.worst_long:<10}  {r.worst_short}")


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run(args) -> None:
    config     = _load_config(args.config)
    score_date = _resolve_score_date(config, args.date)
    cache_dir  = _resolve_cache_dir(config)
    _setup_logging(args.verbose)

    if args.clear_halt:
        clear_halt(cache_dir)
        print("Halt lock cleared.")
        return

    db_url = os.environ.get("DATABASE_URL",
             config.get("database", {}).get("url", "sqlite:///meridian.db"))
    engine = get_engine(db_url)
    initialise_schema(engine)
    nav_usd = float(config.get("portfolio", {}).get("nav_usd", 10_000_000))

    with engine.connect() as conn:
        logger.info("=== Layer 5 Risk Management — %s ===", score_date)

        if is_halted(cache_dir) and not args.whatif:
            print("\n⚠  KILL SWITCH ACTIVE — system is halted. Use --clear-halt to resume.\n")
            if not (args.tail_only or args.pre_trade_only):
                print("Running in read-only mode.")
                args.whatif = True

        risk_state = load_risk_state(cache_dir)
        positions  = load_positions(conn)

        # --- Mode dispatch ---

        if args.tail_only:
            print("\n--- Tail Risk ---")
            tail_result = run_tail_risk(conn, score_date, config, cache_dir, whatif=args.whatif)
            risk_state["tail_risk_state"] = tail_result["tail_risk_state"]
            _print_tail_risk(tail_result)

        elif args.pre_trade_only:
            print("\n--- Pre-Trade Veto ---")
            pre_trade_results = run_pre_trade(conn, score_date, config, cache_dir, whatif=args.whatif)
            _print_pre_trade(pre_trade_results)

        else:
            # Full pipeline

            # 1. Factor risk model
            frm = None
            if not positions.empty:
                print("\n--- Factor Risk Model ---")
                frm = compute_factor_risk(conn, positions, score_date)
                save_predicted_cov(frm, cache_dir, score_date)
                risk_state["risk_decomposition"] = {
                    "factor_var_pct":   round(frm.factor_var_pct, 4),
                    "specific_var_pct": round(frm.specific_var_pct, 4),
                    "annualised_vol":   round(frm.total_vol, 4),
                    "factor_contributions": {k: round(v, 4) for k, v in frm.factor_contributions.items()},
                }
                risk_state["mctr_top5"] = _build_mctr_top5(frm, positions)
                _print_factor_risk(frm)

            # 2. Pre-trade veto
            print("\n--- Pre-Trade Veto ---")
            pre_trade_results = run_pre_trade(conn, score_date, config, cache_dir, whatif=args.whatif)
            _print_pre_trade(pre_trade_results)

            # 3. Circuit breakers
            print("\n--- Circuit Breakers ---")
            risk_state = run_circuit_breakers(conn, score_date, nav_usd, config, risk_state, cache_dir, whatif=args.whatif)
            _print_circuit_breakers(risk_state)

            # 4. Tail risk
            print("\n--- Tail Risk ---")
            tail_result = run_tail_risk(conn, score_date, config, cache_dir, whatif=args.whatif)
            risk_state["tail_risk_state"] = tail_result["tail_risk_state"]
            _print_tail_risk(tail_result)

            # 5. Factor monitor
            print("\n--- Factor Monitor ---")
            factor_alerts = run_factor_monitor(conn, positions, score_date, config, whatif=args.whatif)
            _print_factor_monitor(factor_alerts)

            # 6. Correlation monitor
            print("\n--- Correlation Monitor ---")
            corr_result = run_correlation_monitor(conn, positions, score_date, config, whatif=args.whatif)
            risk_state["correlation_monitor"] = corr_result
            _print_correlation(corr_result)

            # Merge and display all alerts
            all_alerts = list(factor_alerts)
            all_alerts.extend(corr_result.get("alerts", []))
            if frm is not None:
                for ticker in frm.mctr_flags:
                    w_pct  = abs(positions.set_index("ticker")["weight"].get(ticker, 0.0)) * 100
                    mctr_v = float(frm.mctr.get(ticker, 0.0))
                    mctr_pct = abs(mctr_v) / frm.total_vol * 100 if frm.total_vol else 0.0
                    all_alerts.append({
                        "type":     "MCTR_CONCENTRATION",
                        "ticker":   ticker,
                        "priority": "HIGH",
                        "message":  f"MCTR {mctr_pct:.1f}% > 1.5× weight {w_pct:.1f}%",
                    })
            risk_state["alerts"] = all_alerts

            print("\n--- All Alerts ---")
            _print_alerts(all_alerts)

            # Stress tests (full pipeline only)
            if args.stress:
                print("\n--- Stress Tests ---")
                stress_results = run_stress_tests(conn, positions, score_date, config, cache_dir)
                _print_stress(stress_results, nav_usd)

        save_risk_state(risk_state, cache_dir)

        if args.whatif:
            print("\n  (--whatif mode: no changes committed)")

        print("\n=== Done ===")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Layer 5 Risk Management")
    parser.add_argument("--config",         default="config.yaml")
    parser.add_argument("--date",           default=None,         help="Score date YYYY-MM-DD")
    parser.add_argument("--stress",         action="store_true",  help="Run stress tests after full pipeline")
    parser.add_argument("--tail-only",      action="store_true",  dest="tail_only",       help="Tail risk monitors only")
    parser.add_argument("--pre-trade-only", action="store_true",  dest="pre_trade_only",  help="Pre-trade veto only")
    parser.add_argument("--clear-halt",     action="store_true",  dest="clear_halt",      help="Clear kill switch lock and exit")
    parser.add_argument("--whatif",         action="store_true",  help="Run all checks but do not commit changes")
    parser.add_argument("--verbose",        action="store_true")
    return parser.parse_args(argv)


if __name__ == "__main__":
    run(_parse_args())
