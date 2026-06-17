#!/usr/bin/env python3
"""
Meridian Capital Partners — Transcript ingestion (between Layer 2 and Layer 3)

Fetches earnings transcripts from FMP for the current LONG/SHORT candidates
identified by Layer 2 scoring.

Usage:
    python run_transcripts.py [--date 2026-04-01]
"""

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path

import sqlalchemy as sa
import yaml
from dotenv import load_dotenv

_ROOT = Path(__file__).parent
load_dotenv(_ROOT / ".env")
sys.path.insert(0, str(_ROOT))

import factors.db  # noqa: F401, E402
from data.db import get_engine, initialise_schema  # noqa: E402
from data.transcripts import update_transcripts  # noqa: E402
from factors.db import factor_scores  # noqa: E402

logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )


def _load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _candidate_tickers(conn, score_date: str) -> list[str]:
    rows = (
        conn.execute(
            sa.select(factor_scores.c.ticker)
            .where(
                (factor_scores.c.score_date == score_date)
                & (factor_scores.c.direction != "NEUTRAL")
            )
            .order_by(factor_scores.c.composite_score.desc())
        )
        .scalars()
        .all()
    )
    return list(rows)


def main() -> None:
    _setup_logging()

    p = argparse.ArgumentParser(description="Meridian — Transcript ingestion")
    p.add_argument("--date", default=str(date.today()), help="Score date (YYYY-MM-DD)")
    p.add_argument("--config", default=str(_ROOT / "config.yaml"))
    args = p.parse_args()

    config = _load_config(args.config)
    db_url = os.getenv("DATABASE_URL") or config["database"]["url"]
    engine = get_engine(db_url)
    initialise_schema(engine)

    with engine.begin() as conn:
        tickers = _candidate_tickers(conn, args.date)

    if not tickers:
        logger.warning("No LONG/SHORT candidates found for %s — run Layer 2 first", args.date)
        sys.exit(0)

    logger.info("Fetching transcripts for %d candidates: %s", len(tickers), ", ".join(tickers))

    with engine.begin() as conn:
        stats = update_transcripts(conn, tickers, config)

    total = sum(stats.values())
    logger.info("Done — %d transcript(s) stored", total)


if __name__ == "__main__":
    main()
