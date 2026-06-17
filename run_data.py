#!/usr/bin/env python3
"""
Meridian Capital Partners — Layer 1 Data Ingestion Entry Point

Usage:
    python run_data.py [--no-filings] [--no-13f] [--forms 10-K 10-Q] [--force-universe]
"""

import argparse
import logging
import sys
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).parent
load_dotenv(_ROOT / ".env")

sys.path.insert(0, str(_ROOT))

from data.db import get_engine, initialise_schema, insider_transactions, sp500_universe
from data.earnings_calendar import update_earnings_calendar
from data.estimates import update_estimates
from data.fundamentals import update_fundamentals
from data.institutional import update_institutional
from data.market_data import update_prices
from data.providers import Providers
from data.sec_data import update_sec_data
from data.short_interest import update_short_interest
from data.transcripts import update_transcripts
from data.universe import fetch_sp500, get_all_tickers


def _load_config() -> dict:
    with open(_ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def _setup_logging(config: dict) -> None:
    log_path = _ROOT / config["logging"]["log_file"]
    log_path.parent.mkdir(parents=True, exist_ok=True)

    level = getattr(logging, config["logging"]["level"].upper(), logging.INFO)
    fmt = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    logging.basicConfig(
        level=level,
        format=fmt,
        datefmt=datefmt,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_path, mode="a", encoding="utf-8"),
        ],
    )
    # Suppress noisy third-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("yfinance").setLevel(logging.WARNING)
    logging.getLogger("peewee").setLevel(logging.WARNING)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Meridian Capital Partners — Layer 1 Data Ingestion",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--no-filings",
        action="store_true",
        help="Skip SEC EDGAR filings (10-K, 10-Q, 8-K, Form 4) — faster daily runs",
    )
    p.add_argument(
        "--no-13f",
        action="store_true",
        help="Skip 13-F institutional holdings fetch — faster daily runs",
    )
    p.add_argument(
        "--forms",
        nargs="+",
        metavar="FORM",
        default=None,
        help="Selective SEC form types to pull, e.g. --forms 4 10-K",
    )
    p.add_argument(
        "--force-universe",
        action="store_true",
        help="Force refresh of S&P 500 universe from Wikipedia even if cache is fresh",
    )
    p.add_argument(
        "--tickers",
        nargs="+",
        metavar="TICKER",
        default=None,
        help="Run only for specific tickers (useful for testing)",
    )
    return p.parse_args()


def _print_summary(stats: dict) -> None:
    logger = logging.getLogger("run_data")
    sep = "-" * 60
    logger.info(sep)
    logger.info("RUN SUMMARY")
    logger.info(sep)
    for key, val in stats.items():
        logger.info("  %-35s %s", key, val)
    logger.info(sep)


def main() -> None:
    args = _parse_args()
    config = _load_config()
    _setup_logging(config)
    logger = logging.getLogger("run_data")

    logger.info("=" * 60)
    logger.info("Meridian Capital Partners — Layer 1 Data Ingestion")
    logger.info("=" * 60)

    t_start = time.monotonic()

    # Database — DATABASE_URL env var overrides config (useful in containers)
    import os

    import sqlalchemy as sa

    db_url = os.getenv("DATABASE_URL") or config["database"]["url"]
    engine = get_engine(db_url)
    initialise_schema(engine)
    conn = engine.connect()
    logger.info("Database: %s", db_url)

    # Providers
    providers = Providers()

    # -----------------------------------------------------------------------
    # 1. Universe
    # -----------------------------------------------------------------------
    logger.info("[1/8] Universe")
    all_tickers = get_all_tickers(conn, config, force=args.force_universe)
    sp500_count = conn.execute(sa.select(sa.func.count()).select_from(sp500_universe)).scalar()

    if args.tickers:
        # Scoped run: use the explicit list for everything
        price_tickers = args.tickers
        equity_tickers = args.tickers
        logger.info("Scoped run: %d tickers specified", len(price_tickers))
    else:
        # Full run: benchmarks get prices only; equity steps use S&P 500 tickers
        price_tickers = all_tickers
        equity_tickers = fetch_sp500(conn, config)  # already cached from above

    # -----------------------------------------------------------------------
    # 2. Prices  (universe + benchmarks)
    # -----------------------------------------------------------------------
    logger.info("[2/8] Market prices")
    price_stats = update_prices(conn, price_tickers, config, providers)
    bars_added = sum(price_stats.values())

    # -----------------------------------------------------------------------
    # 3. Fundamentals  (equities only — ETFs/benchmarks have no fundamentals)
    # -----------------------------------------------------------------------
    logger.info("[3/8] Fundamentals")
    fund_stats = update_fundamentals(conn, equity_tickers, config, providers)
    periods_added = sum(fund_stats.values())

    # -----------------------------------------------------------------------
    # 4. Short interest  (equities only)
    # -----------------------------------------------------------------------
    logger.info("[4/8] Short interest")
    si_stats = update_short_interest(conn, equity_tickers)
    si_updated = sum(1 for v in si_stats.values() if v)

    # -----------------------------------------------------------------------
    # 5. Analyst estimates  (equities only)
    # -----------------------------------------------------------------------
    logger.info("[5/8] Analyst estimates")
    est_stats = update_estimates(conn, equity_tickers)
    est_updated = sum(1 for v in est_stats.values() if v)

    # -----------------------------------------------------------------------
    # 6. Earnings calendar  (equities only)
    # -----------------------------------------------------------------------
    logger.info("[6/8] Earnings calendar")
    ec_stats = update_earnings_calendar(conn, equity_tickers, config)
    ec_events = sum(ec_stats.values())

    # -----------------------------------------------------------------------
    # 7. SEC Filings (unless --no-filings)
    # -----------------------------------------------------------------------
    filings_stored = 0
    insider_txns = 0
    transcripts_stored = 0
    if args.no_filings:
        logger.info("[7/9] SEC filings — SKIPPED (--no-filings)")
        logger.info("[8/9] Transcript ingestion — SKIPPED (--no-filings)")
    else:
        logger.info("[7/9] SEC filings")
        sec_forms = args.forms or config["sec"]["forms"]
        sec_stats = update_sec_data(conn, equity_tickers, config, forms=sec_forms)
        filings_stored = sum(sec_stats.values())
        insider_txns = conn.execute(
            sa.select(sa.func.count()).select_from(insider_transactions)
        ).scalar()

        logger.info("[8/9] Earnings transcripts (8-K exhibit mining)")
        tx_stats = update_transcripts(conn, equity_tickers, config)
        transcripts_stored = sum(tx_stats.values())

    # -----------------------------------------------------------------------
    # 9. 13-F Institutional holdings (unless --no-13f)
    # -----------------------------------------------------------------------
    inst_holdings = 0
    if args.no_13f:
        logger.info("[9/9] 13-F institutional — SKIPPED (--no-13f)")
    else:
        logger.info("[9/9] 13-F institutional holdings")
        inst_stats = update_institutional(conn, config)
        inst_holdings = sum(inst_stats.values())

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    elapsed = time.monotonic() - t_start
    _print_summary(
        {
            "S&P 500 tickers": sp500_count,
            "Total ticker universe": len(all_tickers),
            "Price bars added": bars_added,
            "Tickers with new prices": sum(1 for v in price_stats.values() if v),
            "Fundamental periods added": periods_added,
            "Short interest updated": f"{si_updated} tickers",
            "Estimates updated": f"{est_updated} tickers",
            "Upcoming earnings events": ec_events,
            "SEC filings cached": filings_stored,
            "Insider transactions": insider_txns,
            "Transcripts stored": transcripts_stored,
            "Institutional holdings": inst_holdings,
            "Elapsed": f"{elapsed:.1f}s",
        }
    )

    conn.close()
    engine.dispose()
    logger.info("Layer 1 ingestion complete")


if __name__ == "__main__":
    main()
