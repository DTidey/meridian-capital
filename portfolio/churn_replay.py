"""Read-only churn replay over the stored score history (packet 64).

Usage:
    python -m portfolio.churn_replay --since 2026-10-27 [--until 2026-11-09]
        [--exit-ranks 20,25,30,35,40] [--smoothing-days 1,3,5,10] [--low-bands 12-18,13-17]

Opens one read-only transaction (PostgreSQL `SET TRANSACTION READ ONLY`, SQLite
`PRAGMA query_only`), runs SELECTs only and never calls the broker. Prints markdown tables:
scoring stability per scoring variant, then churn per scoring variant and exit rank.
"""

import argparse
import logging
import sys
from datetime import date, timedelta
from itertools import pairwise
from pathlib import Path

import pandas as pd
import sqlalchemy as sa
import yaml
from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent

from analysis.db import ai_scores, combined_scores  # noqa: E402
from data.db import daily_prices, database_url, get_engine  # noqa: E402
from factors.db import factor_scores  # noqa: E402
from portfolio import churn_sim  # noqa: E402

logger = logging.getLogger(__name__)

_VIX_HISTORY_DAYS = 60  # calendar days of VIX loaded before --since, for the trailing mean


def set_read_only(conn: sa.engine.Connection) -> None:
    """Make the connection's transaction read-only; must run before any other statement."""
    if conn.dialect.name == "postgresql":
        conn.execute(sa.text("SET TRANSACTION READ ONLY"))
    elif conn.dialect.name == "sqlite":
        conn.execute(sa.text("PRAGMA query_only = ON"))
    else:
        raise RuntimeError(f"churn replay: no read-only mode for dialect {conn.dialect.name}")


def load_history(conn: sa.engine.Connection, since: str, until: str) -> dict:
    """Score dates in [since, until] with stored factor, AI and combined scores, plus VIX."""
    dates = [
        r[0]
        for r in conn.execute(
            sa.select(combined_scores.c.score_date)
            .where(combined_scores.c.score_date.between(since, until))
            .distinct()
            .order_by(combined_scores.c.score_date)
        )
    ]
    fs_cols = ["ticker", "score_date", "sector", "regime", "composite_score", *churn_sim.SCORE_COLS]
    vix_start = (date.fromisoformat(since) - timedelta(days=_VIX_HISTORY_DAYS)).isoformat()
    return {
        "dates": dates,
        "factors": _frame(conn, factor_scores, fs_cols, dates),
        "ai": _frame(
            conn, ai_scores, ["ticker", "score_date", "ai_composite", "analyzers_used"], dates
        ),
        "combined": _frame(
            conn, combined_scores, ["ticker", "score_date", "combined_score", "direction"], dates
        ),
        "vix": pd.DataFrame(
            conn.execute(
                sa.select(daily_prices.c.date, daily_prices.c.close).where(
                    (daily_prices.c.ticker == "^VIX")
                    & daily_prices.c.date.between(vix_start, until)
                )
            ).fetchall(),
            columns=["date", "close"],
        ),
    }


def _frame(conn, table: sa.Table, cols: list[str], dates: list[str]) -> pd.DataFrame:
    rows = conn.execute(
        sa.select(*[table.c[c] for c in cols]).where(table.c.score_date.in_(dates))
    ).fetchall()
    return pd.DataFrame(rows, columns=cols)


def score_nights(history: dict, scoring: churn_sim.Scoring, config: dict) -> dict:
    """Candidates and composites per night under one scoring variant, plus share stats."""
    base = config.get("scoring", {}).get("factor_weights", {})
    nights, composites, shares = [], [], []
    for day in history["dates"]:
        fs = history["factors"][history["factors"]["score_date"] == day]
        if scoring.mode == "stored":
            cands = history["combined"][history["combined"]["score_date"] == day]
            nights.append(cands[cands["direction"].isin(["LONG", "SHORT"])])
            composites.append(fs.set_index("ticker")["composite_score"])
            continue
        regime = fs["regime"].dropna().iloc[0] if fs["regime"].notna().any() else "NORMAL"
        weights, high, low = churn_sim.night_weights(scoring, base, regime, history["vix"], day)
        ai = history["ai"][history["ai"]["score_date"] == day].drop(columns="score_date")
        df = churn_sim.rescore(fs, ai, weights, config)
        nights.append(df[df["direction"].isin(["LONG", "SHORT"])])
        composites.append(df.set_index("ticker")["composite_score"])
        shares.append((high, low))
    return {"nights": nights, "composites": composites, "shares": shares}


def scoring_stats(scored: dict) -> dict:
    """Composite Spearman (mean, min) and blend share movement for one scoring variant."""
    rho = churn_sim.spearman_pairs(scored["composites"])
    stats = {"spearman_mean": sum(rho) / max(len(rho), 1), "spearman_min": min(rho, default=1.0)}
    shares = [s for s in scored["shares"] if s[0] is not None]
    if shares:
        high = [h for h, _ in shares]
        low = [lo for _, lo in shares]
        stats["max_step_high"] = max((abs(b - a) for a, b in pairwise(high)), default=0.0)
        stats["max_step_low"] = max((abs(b - a) for a, b in pairwise(low)), default=0.0)
        stats["nights_low_gt0"] = sum(lo > 0 for lo in low)
        stats["mean_low"] = sum(low) / len(low)
        stats["mean_high"] = sum(high) / len(high)
    return stats


def scorings(config: dict, days_grid: list[int], low_bands: list[tuple]) -> list:
    """Stored scores, today's switch recomputed, and the blend grid."""
    regime_cfg = config.get("scoring", {}).get("regime_weights", {})
    out = [
        churn_sim.Scoring("stored", "stored"),
        churn_sim.Scoring("switch (recomputed)", "switch", regime_config=regime_cfg),
    ]
    for low_full, low_none in low_bands:
        band = {**regime_cfg.get("low_vol", {}), "vix_full": low_full, "vix_none": low_none}
        cfg = {**regime_cfg, "low_vol": band}
        for days in days_grid:
            name = f"blend {days}d low {low_full:g}-{low_none:g}"
            out.append(churn_sim.Scoring(name, "blend", days=days, regime_config=cfg))
    return out


def run_replay(conn, config: dict, args) -> tuple[list[dict], list[dict]]:
    """(scoring rows, churn rows) for every scoring variant and exit rank."""
    set_read_only(conn)
    history = load_history(conn, args.since, args.until)
    score_rows, churn_rows = [], []
    for scoring in scorings(config, args.smoothing_days, args.low_bands):
        scored = score_nights(history, scoring, config)
        score_rows.append({"scoring": scoring.name, **scoring_stats(scored)})
        for rank in args.exit_ranks:
            result = churn_sim.simulate(scored["nights"], config, rank)
            churn_rows.append({"scoring": scoring.name, "exit_rank": rank, **result})
    conn.rollback()
    return score_rows, churn_rows


def _markdown(rows: list[dict]) -> str:
    if not rows:
        return "(no rows)"
    cols = list(dict.fromkeys(k for r in rows for k in r))
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        cells = [
            f"{r.get(c, ''):.3f}" if isinstance(r.get(c), float) else str(r.get(c, ""))
            for c in cols
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _int_list(text: str) -> list[int]:
    return [int(x) for x in text.split(",") if x]


def _bands(text: str) -> list[tuple[float, float]]:
    return [tuple(float(v) for v in b.split("-")) for b in text.split(",") if b]


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Read-only Layer 4 churn replay (packet 64)")
    p.add_argument("--config", default=str(_ROOT / "config.yaml"))
    p.add_argument("--since", default="2026-10-27")
    p.add_argument("--until", default=date.today().isoformat())
    p.add_argument("--exit-ranks", type=_int_list, default=[20, 25, 30, 35, 40])
    p.add_argument("--smoothing-days", type=_int_list, default=[1, 3, 5, 10])
    p.add_argument("--low-bands", type=_bands, default=[(12.0, 18.0), (13.0, 17.0), (14.0, 16.0)])
    return p.parse_args(argv)


def main(argv=None) -> None:
    load_dotenv(_ROOT / ".env")
    args = parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")
    with open(args.config) as f:
        config = yaml.safe_load(f)
    engine = get_engine(database_url())
    with engine.connect() as conn:
        score_rows, churn_rows = run_replay(conn, config, args)
    engine.dispose()
    print(f"## Scoring stability ({args.since} to {args.until})\n")
    print(_markdown(score_rows))
    print(f"\n## Churn (idealised book, {args.since} to {args.until})\n")
    print(_markdown(churn_rows))


if __name__ == "__main__":
    sys.path.insert(0, str(_ROOT))
    main()
