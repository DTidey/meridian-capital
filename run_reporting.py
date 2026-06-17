#!/usr/bin/env python3
"""
Meridian Capital Partners — Layer 7: Reporting Engine

Usage:
    python run_reporting.py                     # NAV + attribution + FIFO trades
    python run_reporting.py --tearsheet         # + tear sheet markdown
    python run_reporting.py --commentary        # + weekly JARVIS commentary (if due)
    python run_reporting.py --letter            # + daily LP letter
    python run_reporting.py --all               # all of the above
    python run_reporting.py --date YYYY-MM-DD   # target a specific date
"""

import argparse
import logging
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

_ROOT = Path(__file__).parent
load_dotenv(_ROOT / ".env")
sys.path.insert(0, str(_ROOT))

import reporting.db        # noqa: F401, E402 — registers tables
import execution.db        # noqa: F401, E402
import risk.db             # noqa: F401, E402
import portfolio.db        # noqa: F401, E402
import analysis.db         # noqa: F401, E402
import factors.db          # noqa: F401, E402

from data.db import get_engine, initialise_schema    # noqa: E402
from reporting.nav_series          import build_nav_series    # noqa: E402
from reporting.pnl_attribution     import run as run_attribution   # noqa: E402
from reporting.position_attribution import build_trades            # noqa: E402
from reporting.tear_sheet          import write as write_tear_sheet # noqa: E402
from reporting.commentary          import generate_if_due          # noqa: E402
from reporting.lp_letter           import generate as generate_letter  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("run_reporting")


def _load_cfg(root: Path) -> dict:
    with open(root / "config.yaml") as fh:
        return yaml.safe_load(fh)


def main() -> None:
    parser = argparse.ArgumentParser(description="Meridian Layer 7 — Reporting Engine")
    parser.add_argument("--date",        default=None, help="Override report date (YYYY-MM-DD)")
    parser.add_argument("--tearsheet",   action="store_true")
    parser.add_argument("--commentary",  action="store_true")
    parser.add_argument("--letter",      action="store_true")
    parser.add_argument("--all",         action="store_true")
    args = parser.parse_args()

    do_tearsheet  = args.tearsheet  or args.all
    do_commentary = args.commentary or args.all
    do_letter     = args.letter     or args.all

    cfg    = _load_cfg(_ROOT)
    db_url = os.environ.get("DATABASE_URL", cfg["database"]["url"])
    engine = get_engine(db_url)
    initialise_schema(engine)

    cfg_port    = cfg.get("portfolio", {})
    nav_usd     = float(cfg_port.get("nav_usd", 10_000_000))
    etf_map     = cfg.get("scoring", {}).get("sector_etf_map") or {}
    output_dir  = Path(cfg.get("reporting", {}).get("attribution_csv", "output/daily_attribution.csv")).parent
    output_dir.mkdir(parents=True, exist_ok=True)

    # Step 1 — NAV series
    log.info("=== Step 1/3: Building NAV series ===")
    build_nav_series(engine, nav_usd=nav_usd)

    # Step 2 — P&L attribution
    log.info("=== Step 2/3: P&L attribution ===")
    attr_csv = cfg.get("reporting", {}).get("attribution_csv", "output/daily_attribution.csv")
    run_attribution(engine, output_csv=attr_csv, sector_etf_map=etf_map or None)

    # Step 3 — FIFO position trades
    log.info("=== Step 3/3: FIFO position trades ===")
    build_trades(engine)

    if do_tearsheet:
        log.info("=== Tear Sheet ===")
        tear_path = cfg.get("reporting", {}).get("tear_sheet_path", "output/tear_sheet.md")
        inception  = cfg.get("reporting", {}).get("inception_date", "2024-01-02")
        write_tear_sheet(engine, cfg=cfg, output_path=tear_path, inception_date=inception)

    if do_commentary:
        log.info("=== Weekly Commentary ===")
        result = generate_if_due(engine, cfg=cfg)
        if result:
            log.info("Commentary generated (%d chars)", len(result))
        else:
            log.info("Commentary not due today")

    if do_letter:
        log.info("=== Daily LP Letter ===")
        content = generate_letter(engine, cfg=cfg, letter_date=args.date)
        log.info("LP letter generated (%d chars)", len(content))

    log.info("Reporting complete.")


if __name__ == "__main__":
    main()
