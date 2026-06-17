#!/usr/bin/env python3
"""
Meridian Capital Partners — Layer 4: Portfolio Construction

Usage:
    python run_portfolio.py --rebalance [--optimize-method mvo|conviction]
    python run_portfolio.py --whatif    [--optimize-method mvo|conviction]
    python run_portfolio.py --current
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

import portfolio.db  # noqa: F401, E402
import analysis.db   # noqa: F401, E402
import factors.db    # noqa: F401, E402

from data.db import get_engine, initialise_schema, daily_prices, sp500_universe  # noqa: E402
from analysis.db import combined_scores as combined_scores_table                  # noqa: E402
from factors.db import factor_scores as factor_scores_table                       # noqa: E402
from portfolio.state import load_positions, save_positions, get_nav               # noqa: E402
from portfolio.beta import compute_betas, portfolio_beta                          # noqa: E402
from portfolio.factor_exposure import compute_exposures                            # noqa: E402
from portfolio.rebalance_schedule import check_events                             # noqa: E402
from portfolio import optimizer as tilt_opt                                        # noqa: E402
from portfolio import mvo_optimizer as mvo_opt                                    # noqa: E402
from portfolio.rebalance import generate_trades                                    # noqa: E402

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
    cfg = config.get("portfolio", {}).get("score_date")
    if cfg:
        return str(cfg)
    return str(date.today())


def _load_candidates(conn, score_date: str) -> pd.DataFrame:
    rows = conn.execute(
        sa.select(
            combined_scores_table.c.ticker,
            combined_scores_table.c.combined_score,
            combined_scores_table.c.direction,
        ).where(
            (combined_scores_table.c.score_date == score_date) &
            (combined_scores_table.c.direction.in_(["LONG", "SHORT"]))
        )
    ).fetchall()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=["ticker", "combined_score", "direction"])

    # Attach sector from factor_scores
    sector_rows = conn.execute(
        sa.select(factor_scores_table.c.ticker, factor_scores_table.c.sector)
        .where(factor_scores_table.c.score_date == score_date)
    ).fetchall()
    sector_map = {r[0]: r[1] for r in sector_rows}
    df["sector"] = df["ticker"].map(sector_map).fillna("Unknown")
    return df


def _load_prices(conn, tickers: list[str], score_date: str, lookback: int = 130) -> dict[str, pd.DataFrame]:
    if not tickers:
        return {}
    rows = conn.execute(
        sa.select(
            daily_prices.c.ticker, daily_prices.c.date,
            daily_prices.c.open, daily_prices.c.high, daily_prices.c.low,
            daily_prices.c.close, daily_prices.c.adj_close, daily_prices.c.volume,
        ).where(
            daily_prices.c.ticker.in_(tickers + ["SPY"]) &
            (daily_prices.c.date <= score_date)
        ).order_by(daily_prices.c.date.asc())
    ).fetchall()

    price_map: dict[str, pd.DataFrame] = {}
    cols = ["ticker", "date", "open", "high", "low", "close", "adj_close", "volume"]
    df   = pd.DataFrame(rows, columns=cols)
    for ticker, group in df.groupby("ticker"):
        price_map[ticker] = group.drop(columns="ticker").tail(lookback).reset_index(drop=True)
    return price_map


def _load_factor_scores(conn, tickers: list[str], score_date: str) -> pd.DataFrame:
    if not tickers:
        return pd.DataFrame()
    rows = conn.execute(
        sa.select(factor_scores_table).where(
            (factor_scores_table.c.score_date == score_date) &
            (factor_scores_table.c.ticker.in_(tickers))
        )
    ).fetchall()
    cols = [c.name for c in factor_scores_table.columns]
    return pd.DataFrame(rows, columns=cols)


def _print_portfolio_summary(portfolio_df: pd.DataFrame, betas: pd.Series, nav: float) -> None:
    if portfolio_df.empty:
        print("  (empty)")
        return

    long_df  = portfolio_df[portfolio_df["direction"] == "LONG"]
    short_df = portfolio_df[portfolio_df["direction"] == "SHORT"]

    long_gross  = long_df["weight"].abs().sum()
    short_gross = short_df["weight"].abs().sum()
    net         = long_df["weight"].sum() + short_df["weight"].sum()

    all_w = portfolio_df.set_index("ticker")["weight"]
    net_beta = portfolio_beta(all_w, betas)

    print(f"  Positions   : {len(long_df)} long / {len(short_df)} short")
    print(f"  Gross exp   : {long_gross + short_gross:.1%}")
    print(f"  Net exp     : {net:.1%}")
    print(f"  Net beta    : {net_beta:.3f}")
    print()

    print("  Sector breakdown (net weight):")
    if "sector" in portfolio_df.columns:
        sect = (portfolio_df.groupby("sector")["weight"].sum()
                .sort_values(key=abs, ascending=False))
        for sector, w in sect.items():
            print(f"    {sector:<30} {w:+.2%}")
    print()


def _print_trade_list(trades: pd.DataFrame) -> None:
    active = trades[trades["action"] != "HOLD"].sort_values("priority")
    if active.empty:
        print("  No trades required.")
        return
    print(f"  {'#':<4} {'Ticker':<8} {'Action':<8} {'Price':>9} {'Cur Shrs':>10}"
          f" {'Tgt Shrs':>10} {'Delta':>10} {'Est. Cost':>10}")
    print("  " + "-" * 78)
    for _, row in active.iterrows():
        price = row.get("price", 0.0) or 0.0
        print(f"  {row['priority']:<4} {row['ticker']:<8} {row['action']:<8}"
              f" {price:>9.2f}"
              f" {row['current_shares']:>10.0f} {row['target_shares']:>10.0f}"
              f" {row['delta_shares']:>+10.0f} ${row['estimated_cost_usd']:>9.2f}")
    total_cost = active["estimated_cost_usd"].sum()
    print(f"\n  Total estimated transaction costs: ${total_cost:,.2f}")


def _print_current(conn) -> None:
    positions = load_positions(conn)
    if positions.empty:
        print("No open positions.")
        return
    print(f"\n{'Ticker':<8} {'Dir':<6} {'Shares':>10} {'Entry':>8} {'Current':>8}"
          f" {'Mkt Value':>12} {'Unreal PnL':>12} {'Sector'}")
    print("-" * 90)
    for _, row in positions.sort_values("direction").iterrows():
        print(f"{row['ticker']:<8} {row.get('direction',''):<6}"
              f" {row.get('shares', 0):>10.0f}"
              f" {row.get('entry_price', 0):>8.2f}"
              f" {row.get('current_price', 0):>8.2f}"
              f" {row.get('market_value', 0):>12,.0f}"
              f" {row.get('unrealized_pnl', 0):>+12,.0f}"
              f" {row.get('sector', '')}")


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run(args) -> None:
    config     = _load_config(args.config)
    score_date = _resolve_score_date(config, args.date)
    _setup_logging(args.verbose)

    db_url = os.environ.get("DATABASE_URL",
             config.get("database", {}).get("url", "sqlite:///meridian.db"))
    engine = get_engine(db_url)
    initialise_schema(engine)

    with engine.connect() as conn:
        if args.current:
            _print_current(conn)
            return

        logger.info("=== Layer 4 Portfolio Construction — %s ===", score_date)

        # 1. Load candidates (LONG/SHORT only)
        candidates = _load_candidates(conn, score_date)
        if candidates.empty:
            logger.error("No LONG/SHORT candidates for %s — run Layer 3 first", score_date)
            sys.exit(0)
        logger.info("Loaded %d candidates", len(candidates))

        # 2. Prices and betas
        all_tickers = candidates["ticker"].tolist()
        prices = _load_prices(conn, all_tickers, score_date,
                              lookback=config.get("portfolio", {}).get("mvo", {})
                              .get("cov_lookback_days", 130) + 10)
        beta_days = config.get("portfolio", {}).get("beta_lookback_days", 60)
        betas     = compute_betas(conn, all_tickers, score_date, lookback_days=beta_days)

        # 3. Rebalance schedule warnings
        warnings = check_events(all_tickers, score_date, conn, config)
        if warnings:
            print("\n⚠  Rebalance Schedule Warnings:")
            for w in warnings:
                print(f"   {w}")
            print()

        # 4. Optimise
        opt_method = args.optimize_method
        logger.info("Running %s optimiser", opt_method)
        optimizer = mvo_opt if opt_method == "mvo" else tilt_opt
        nav       = get_nav(config)

        target = optimizer.optimise(candidates, prices, betas, config, score_date, conn)
        if target.empty:
            logger.error("Optimiser returned empty portfolio")
            sys.exit(1)

        # 5. Factor exposures
        factor_df = _load_factor_scores(conn, all_tickers, score_date)
        target_with_weight = target.copy()
        exposures = compute_exposures(target_with_weight, factor_df)

        print("\n=== Target Portfolio ===")
        _print_portfolio_summary(target, betas, nav)

        print("  Factor exposures (long − short spread):")
        for factor, spread in exposures["spread"].items():
            print(f"    {factor:<28} {spread:+.1f}")
        print()

        # 6. Trade list
        current = load_positions(conn)
        commit  = not args.whatif
        trades  = generate_trades(current, target, prices, config, conn, score_date, commit=commit)

        print("=== Trade List ===")
        _print_trade_list(trades)

        if args.whatif:
            print("\n  (--whatif mode: no changes committed)")
        else:
            logger.info("Portfolio committed for %s", score_date)

    logger.info("=== Done ===")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Layer 4 Portfolio Construction")
    parser.add_argument("--config",          default="config.yaml")
    parser.add_argument("--date",            default=None,         help="Score date YYYY-MM-DD")
    parser.add_argument("--rebalance",       action="store_true",  help="Run full rebalance")
    parser.add_argument("--whatif",          action="store_true",  help="Preview only, no commit")
    parser.add_argument("--current",         action="store_true",  help="Show current positions")
    parser.add_argument("--optimize-method", default="conviction",
                        choices=["conviction", "mvo"],             help="Optimisation method")
    parser.add_argument("--verbose",         action="store_true")
    return parser.parse_args(argv)


if __name__ == "__main__":
    run(_parse_args())
