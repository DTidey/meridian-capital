#!/usr/bin/env python3
"""
Meridian Capital Partners — Layer 2: Factor Scoring Engine

Usage:
    python run_scoring.py [--ticker AAPL] [--date 2026-04-01] [--no-crowding]
"""

import argparse
import logging
import os
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd
import yaml
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Bootstrap — must happen before any local imports
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).parent
load_dotenv(_ROOT / ".env")
sys.path.insert(0, str(_ROOT))

# Register Layer 2 tables on the shared metadata before calling initialise_schema
import factors.db  # noqa: F401, E402
from data.db import get_engine, initialise_schema, insert_or_replace  # noqa: E402
from factors import (  # noqa: E402
    composite as composite_mod,
)
from factors import (
    crowding as crowding_mod,
)
from factors import (
    growth as growth_mod,
)
from factors import (
    insider as insider_mod,
)
from factors import (
    institutional as institutional_mod,
)
from factors import (
    momentum as momentum_mod,
)
from factors import (
    quality as quality_mod,
)
from factors import (
    regime_weights as regime_mod,
)
from factors import (
    revisions as revisions_mod,
)
from factors import (
    short_interest as si_mod,
)
from factors import (
    value as value_mod,
)
from factors.db import crowding_flags, factor_scores, regime_state  # noqa: E402
from factors.loader import load_scoring_data  # noqa: E402


def _load_config() -> dict:
    with open(_ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def _setup_logging(config: dict) -> None:
    log_path = _ROOT / config["logging"]["log_file"]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, config["logging"]["level"].upper(), logging.INFO)
    fmt = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
    logging.basicConfig(
        level=level,
        format=fmt,
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_path, mode="a", encoding="utf-8"),
        ],
    )
    for noisy in ("urllib3", "requests", "yfinance", "peewee"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Meridian — Layer 2 Factor Scoring")
    p.add_argument("--ticker", metavar="TICKER", help="Single ticker mode")
    p.add_argument("--date", metavar="YYYY-MM-DD", help="Override score date")
    p.add_argument("--no-crowding", action="store_true", help="Skip crowding detection")
    return p.parse_args()


def _write_factor_scores(conn, scored: pd.DataFrame, score_date: str) -> None:
    computed_at = pd.Timestamp.now("UTC").isoformat()
    rows = []
    for ticker, row in scored.iterrows():
        d = {"ticker": ticker, "score_date": score_date, "computed_at": computed_at}
        for col in factor_scores.columns:
            if col.name in ("ticker", "score_date", "computed_at"):
                continue
            d[col.name] = row.get(col.name)
        rows.append(d)
    if rows:
        stmt = insert_or_replace(conn, factor_scores)
        conn.execute(stmt, rows)
        conn.commit()


def _write_regime_state(conn, score_date: str, vix_close, regime: str) -> None:
    stmt = insert_or_replace(conn, regime_state)
    conn.execute(
        stmt,
        [
            {
                "score_date": score_date,
                "vix_close": vix_close,
                "regime": regime,
                "computed_at": pd.Timestamp.now("UTC").isoformat(),
            }
        ],
    )
    conn.commit()


def _write_crowding_flags(conn, flags: list[dict]) -> None:
    if not flags:
        return
    stmt = insert_or_replace(conn, crowding_flags)
    conn.execute(stmt, flags)
    conn.commit()


def _print_summary(
    score_date: str,
    regime: str,
    vix_close,
    scored: pd.DataFrame,
    n_crowding_flags: int,
    elapsed: float,
    logger,
) -> None:
    sep = "=" * 60
    logger.info(sep)
    logger.info("Layer 2 Scoring Complete")
    logger.info(sep)
    logger.info("  Score date       : %s", score_date)
    vix_str = f"{vix_close:.1f}" if vix_close else "N/A"
    logger.info("  Regime           : %s (VIX %s)", regime, vix_str)
    logger.info("  Universe scored  : %d tickers", len(scored))

    n_long = (scored.get("direction", pd.Series()) == "LONG").sum()
    n_short = (scored.get("direction", pd.Series()) == "SHORT").sum()
    logger.info("  LONG candidates  : %d", n_long)
    logger.info("  SHORT candidates : %d", n_short)
    logger.info("  Crowding flags   : %d", n_crowding_flags)
    logger.info("  Elapsed          : %.1fs", elapsed)

    if "composite_score" in scored.columns and "direction" in scored.columns:
        longs = scored[scored["direction"] == "LONG"].nlargest(5, "composite_score")
        shorts = scored[scored["direction"] == "SHORT"].nsmallest(5, "composite_score")

        if not longs.empty:
            logger.info("\nTop 5 LONG candidates:")
            for ticker, row in longs.iterrows():
                logger.info(
                    "  %-6s  Composite: %5.1f  Mom: %5.1f  Qual: %5.1f  Val: %5.1f",
                    ticker,
                    row.get("composite_score", 0),
                    row.get("momentum_score", 0),
                    row.get("quality_score", 0),
                    row.get("value_score", 0),
                )

        if not shorts.empty:
            logger.info("\nTop 5 SHORT candidates:")
            for ticker, row in shorts.iterrows():
                logger.info(
                    "  %-6s  Composite: %5.1f  Mom: %5.1f  Qual: %5.1f  Val: %5.1f",
                    ticker,
                    row.get("composite_score", 0),
                    row.get("momentum_score", 0),
                    row.get("quality_score", 0),
                    row.get("value_score", 0),
                )

    logger.info(sep)


def main() -> None:
    args = _parse_args()
    config = _load_config()
    _setup_logging(config)
    logger = logging.getLogger("run_scoring")

    scoring_cfg = config.get("scoring", {})

    # Score date
    score_date = args.date or (scoring_cfg.get("score_date") or date.today().isoformat())
    logger.info("=" * 60)
    logger.info("Meridian Capital Partners — Layer 2 Factor Scoring")
    logger.info("Score date: %s", score_date)
    logger.info("=" * 60)

    t_start = time.monotonic()

    # Database
    db_url = os.getenv("DATABASE_URL") or config["database"]["url"]
    engine = get_engine(db_url)
    initialise_schema(engine)
    conn = engine.connect()
    logger.info("Database: %s", db_url)

    # ---------------------------------------------------------------------------
    # 1. Load data from Layer 1
    # ---------------------------------------------------------------------------
    logger.info("[1/7] Loading Layer 1 data")
    data = load_scoring_data(conn, config, score_date)

    universe = data["universe"]
    if args.ticker:
        universe = universe[universe["ticker"] == args.ticker.upper()]
        data["universe"] = universe
        logger.info("Single-ticker mode: %s", args.ticker.upper())

    if universe.empty:
        logger.error("No tickers in universe — aborting")
        conn.close()
        engine.dispose()
        sys.exit(1)

    # ---------------------------------------------------------------------------
    # 2. Resolve regime
    # ---------------------------------------------------------------------------
    logger.info("[2/7] Resolving regime")
    regime, vix_close = regime_mod.resolve_regime(data["vix"])
    logger.info("Regime: %s (VIX: %s)", regime, vix_close)

    base_weights = scoring_cfg.get("factor_weights", {})
    if scoring_cfg.get("regime_conditional_weights", True):
        weights = regime_mod.adjust_weights(
            base_weights, regime, scoring_cfg.get("regime_weights", {})
        )
    else:
        weights = base_weights

    # ---------------------------------------------------------------------------
    # 3. Compute all 8 factor scores
    # ---------------------------------------------------------------------------
    logger.info("[3/7] Computing factor scores")
    factor_results = {
        "momentum": momentum_mod.compute(data, config),
        "value": value_mod.compute(data, config),
        "quality": quality_mod.compute(data, config),
        "growth": growth_mod.compute(data, config),
        "revisions": revisions_mod.compute(data, config),
        "short_interest": si_mod.compute(data, config),
        "insider": insider_mod.compute(data, config),
        "institutional": institutional_mod.compute(data, config),
    }
    for name, df in factor_results.items():
        logger.debug("  %s: %d tickers scored", name, len(df))

    # ---------------------------------------------------------------------------
    # 4. Composite score + labelling
    # ---------------------------------------------------------------------------
    logger.info("[4/7] Computing composite scores")
    scored = composite_mod.compute(factor_results, universe, weights, config)
    scored["regime"] = regime

    # ---------------------------------------------------------------------------
    # 5. Write to database
    # ---------------------------------------------------------------------------
    logger.info("[5/7] Writing scores to database")
    _write_factor_scores(conn, scored, score_date)
    _write_regime_state(conn, score_date, vix_close, regime)

    # ---------------------------------------------------------------------------
    # 6. Crowding detection
    # ---------------------------------------------------------------------------
    n_crowding_flags = 0
    if args.no_crowding:
        logger.info("[6/7] Crowding detection — SKIPPED (--no-crowding)")
    else:
        logger.info("[6/7] Crowding detection")
        crowding_cfg = scoring_cfg.get("crowding", {})
        flags = crowding_mod.detect(conn, data["prices"], score_date, crowding_cfg)
        _write_crowding_flags(conn, flags)
        n_crowding_flags = sum(f["flagged"] for f in flags)

    # ---------------------------------------------------------------------------
    # 7. Export CSV
    # ---------------------------------------------------------------------------
    logger.info("[7/7] Writing output CSV")
    csv_path = _ROOT / scoring_cfg.get("output_csv", "output/scored_universe_latest.csv")
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    scored.to_csv(csv_path)
    logger.info("CSV written: %s", csv_path)

    elapsed = time.monotonic() - t_start
    _print_summary(score_date, regime, vix_close, scored, n_crowding_flags, elapsed, logger)

    conn.close()
    engine.dispose()


if __name__ == "__main__":
    main()
