#!/usr/bin/env python3
"""
Meridian Capital Partners — Layer 3: AI Analysis Engine

Usage:
    python run_analysis.py [--date 2026-04-01] [--ticker AAPL] [--no-cache]
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import date
from pathlib import Path

import sqlalchemy as sa
import yaml
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).parent
load_dotenv(_ROOT / ".env")
sys.path.insert(0, str(_ROOT))

import analysis.db  # noqa: F401, E402
import factors.db   # noqa: F401, E402

from data.db import get_engine, initialise_schema           # noqa: E402
from analysis.api_client import OpenAIClient                # noqa: E402
from analysis.cache import AnalysisCache                    # noqa: E402
from analysis.cost_tracker import CostTracker               # noqa: E402
from analysis import (                                       # noqa: E402
    earnings_analyzer,
    filing_analyzer,
    risk_analyzer,
    insider_analyzer,
)
from analysis.sector_analysis import analyse_sectors        # noqa: E402
from analysis.combined_score import (                        # noqa: E402
    compute_ai_composite,
    compute_combined_scores,
)
from analysis.report_generator import generate_reports       # noqa: E402
from factors.db import factor_scores as factor_scores_table  # noqa: E402

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
    cfg_date = config.get("analysis", {}).get("score_date")
    if cfg_date:
        return str(cfg_date)
    return str(date.today())


def _load_long_short_tickers(conn, score_date: str, single_ticker: str | None) -> list[dict]:
    if single_ticker:
        return [{"ticker": single_ticker, "composite_score": None,
                 "direction": "LONG", "sector": None}]

    rows = conn.execute(
        sa.select(
            factor_scores_table.c.ticker,
            factor_scores_table.c.composite_score,
            factor_scores_table.c.direction,
            factor_scores_table.c.sector,
        ).where(
            (factor_scores_table.c.score_date == score_date) &
            (factor_scores_table.c.direction  != "NEUTRAL")
        ).order_by(factor_scores_table.c.composite_score.desc())
    ).fetchall()

    return [
        {"ticker": r[0], "composite_score": r[1], "direction": r[2], "sector": r[3]}
        for r in rows
    ]


def _load_factor_scores_map(conn, tickers: list[str], score_date: str) -> dict[str, dict]:
    if not tickers:
        return {}
    rows = conn.execute(
        sa.select(factor_scores_table).where(
            (factor_scores_table.c.score_date == score_date) &
            (factor_scores_table.c.ticker.in_(tickers))
        )
    ).fetchall()
    keys = [c.name for c in factor_scores_table.columns]
    return {r[0]: dict(zip(keys, r)) for r in rows}


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run(args) -> None:
    config     = _load_config(args.config)
    score_date = _resolve_score_date(config, args.date)
    _setup_logging(args.verbose)

    logger.info("=== Layer 3 Analysis — %s ===", score_date)

    db_url = os.environ.get("DATABASE_URL",
             config.get("database", {}).get("url", "sqlite:///meridian.db"))
    engine = get_engine(db_url)
    initialise_schema(engine)

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        logger.error("OPENAI_API_KEY not set — aborting")
        sys.exit(1)

    analysis_cfg  = config.get("analysis", {})
    cost_ceiling  = analysis_cfg.get("cost_ceiling_usd", 25.0)
    cache_ttl     = analysis_cfg.get("cache_ttl_days",    30)
    default_model = analysis_cfg.get("openai_model",      "gpt-4o")

    tracker = CostTracker(ceiling_usd=cost_ceiling)
    client  = OpenAIClient(api_key=api_key, model=default_model, cost_tracker=tracker)

    with engine.connect() as conn:
        cache     = AnalysisCache(conn, ttl_days=cache_ttl)
        evicted   = cache.evict_expired()
        if evicted:
            logger.info("Cache: evicted %d stale entries", evicted)

        if args.no_cache:
            logger.info("Cache disabled for this run (--no-cache)")
            cache.get = lambda *_: None  # type: ignore[method-assign]

        candidates = _load_long_short_tickers(conn, score_date, args.ticker)
        if not candidates:
            logger.warning("No LONG/SHORT candidates for %s — run Layer 2 first", score_date)
            sys.exit(0)

        logger.info("Analysing %d candidates", len(candidates))

        analyzer_results: dict[str, dict] = {}
        ai_composite_records: list[dict]  = []

        t0 = time.time()
        for i, cand in enumerate(candidates, 1):
            ticker = cand["ticker"]
            logger.info("[%d/%d] %s", i, len(candidates), ticker)

            try:
                earnings = earnings_analyzer.analyse(conn, ticker, client, cache, config, score_date)
                filing   = filing_analyzer.analyse(conn, ticker, client, cache, config, score_date)
                risk     = risk_analyzer.analyse(conn, ticker, client, cache, config, score_date)
                insider  = insider_analyzer.analyse(conn, ticker, client, cache, config, score_date)
            except Exception as exc:
                logger.warning("Skipping %s due to error: %s", ticker, exc)
                earnings = filing = risk = insider = None

            analyzer_results[ticker] = {
                "earnings": earnings,
                "filing":   filing,
                "risk":     risk,
                "insider":  insider,
            }

            rec = compute_ai_composite(conn, ticker, score_date,
                                       earnings, filing, risk, insider)
            ai_composite_records.append(rec)

        elapsed = time.time() - t0
        logger.info("Analyzers done in %.1fs — cost so far: $%.4f",
                    elapsed, tracker.summary()["total_cost_usd"])

        # Step 2: Sector analysis
        logger.info("Running sector analysis ...")
        sector_results = analyse_sectors(candidates, analyzer_results, client, config)
        if sector_results:
            out_dir = analysis_cfg.get("output_dir", "output/reports")
            sector_path = Path(f"{out_dir}_{score_date.replace('-', '')}/sector_analysis.json")
            sector_path.parent.mkdir(parents=True, exist_ok=True)
            sector_path.write_text(json.dumps(sector_results, indent=2))
            logger.info("Sector analysis written to %s", sector_path)

        # Step 3: Combined scores
        logger.info("Computing combined scores ...")
        combined_df = compute_combined_scores(conn, score_date, config)
        if not combined_df.empty:
            logger.info("Combined scores: %d tickers", len(combined_df))

        # Step 4: Reports
        logger.info("Generating reports ...")
        factor_map = _load_factor_scores_map(
            conn, [c["ticker"] for c in candidates], score_date
        )
        written = generate_reports(
            conn, score_date, analyzer_results, factor_map,
            sector_results, config,
        )
        logger.info("Wrote %d reports", len(written))

    summary = tracker.summary()
    total_tokens = summary["prompt_tokens"] + summary["completion_tokens"]
    logger.info(
        "=== Done | API calls: %d | Tokens: %d | Cost: $%.4f ===",
        summary["calls"],
        total_tokens,
        summary["total_cost_usd"],
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Layer 3 AI Analysis")
    parser.add_argument("--config",   default="config.yaml",  help="Config file path")
    parser.add_argument("--date",     default=None,            help="Score date (YYYY-MM-DD)")
    parser.add_argument("--ticker",   default=None,            help="Analyse a single ticker")
    parser.add_argument("--no-cache", action="store_true",     help="Bypass the analysis cache")
    parser.add_argument("--verbose",  action="store_true",     help="Enable debug logging")
    return parser.parse_args(argv)


if __name__ == "__main__":
    run(_parse_args())
