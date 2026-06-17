"""Combine Layer 2 quant composite with Layer 3 AI scores."""

import logging
from datetime import UTC, datetime

import pandas as pd
import sqlalchemy as sa

from analysis.db import ai_scores as ai_scores_table
from analysis.db import combined_scores as combined_scores_table
from analysis.earnings_analyzer import earnings_score as _earnings_score
from analysis.filing_analyzer import filing_score as _filing_score
from analysis.insider_analyzer import insider_score as _insider_score
from analysis.risk_analyzer import risk_score as _risk_score
from data.db import insert_or_replace
from factors._utils import sector_rank
from factors.db import factor_scores as factor_scores_table

logger = logging.getLogger(__name__)


def compute_ai_composite(
    conn: sa.engine.Connection,
    ticker: str,
    score_date: str,
    earnings_result: dict | None,
    filing_result: dict | None,
    risk_result: dict | None,
    insider_result: dict | None,
) -> dict:
    """Compute and persist the AI composite score for one ticker."""
    e_score = _earnings_score(earnings_result)
    f_score = _filing_score(filing_result)
    r_score = _risk_score(risk_result)
    i_score = _insider_score(insider_result)

    available = [s for s in [e_score, f_score, r_score, i_score] if s is not None]
    ai_composite = sum(available) / len(available) if available else None
    analyzers_used = len(available)

    now = datetime.now(UTC).isoformat(timespec="seconds")
    stmt = insert_or_replace(conn, ai_scores_table)
    conn.execute(
        stmt,
        [
            {
                "ticker": ticker,
                "score_date": score_date,
                "earnings_score": e_score,
                "filing_score": f_score,
                "risk_score": r_score,
                "insider_ai_score": i_score,
                "ai_composite": ai_composite,
                "analyzers_used": analyzers_used,
                "computed_at": now,
            }
        ],
    )
    conn.commit()

    return {
        "ticker": ticker,
        "earnings_score": e_score,
        "filing_score": f_score,
        "risk_score": r_score,
        "insider_ai_score": i_score,
        "ai_composite": ai_composite,
        "analyzers_used": analyzers_used,
    }


def compute_combined_scores(
    conn: sa.engine.Connection,
    score_date: str,
    config: dict,
) -> pd.DataFrame:
    """Blend Layer 2 and Layer 3 scores, re-rank within sector, write to DB.

    Returns a DataFrame with columns: ticker, sector, quant_composite,
    ai_composite, combined_score, direction.
    """
    scoring_cfg = config.get("scoring", {})
    analysis_cfg = config.get("analysis", {})
    combined_cfg = analysis_cfg.get("combined_score", {})
    quant_weight = float(combined_cfg.get("quant_weight", 0.60))
    ai_weight = float(combined_cfg.get("ai_weight", 0.40))
    long_thresh = scoring_cfg.get("long_quintile_threshold", 80)
    short_thresh = scoring_cfg.get("short_quintile_threshold", 20)
    min_sector = scoring_cfg.get("min_sector_size", 5)

    quant_df = _load_quant_scores(conn, score_date)
    ai_df = _load_ai_scores(conn, score_date)

    if quant_df.empty:
        logger.warning("Combined: no quant scores for %s", score_date)
        return pd.DataFrame()

    df = quant_df.merge(ai_df, on="ticker", how="left")
    df["analyzers_used"] = df["analyzers_used"].fillna(0).astype(int)

    import numpy as np

    ai_normalised = (df["ai_composite"] - 1) / 9 * 100
    has_ai = (df["analyzers_used"] > 0).values

    blended = quant_weight * df["composite_score"] + ai_weight * ai_normalised
    df["combined_raw"] = np.where(has_ai, blended, df["composite_score"])

    sectors = df.set_index("ticker")["sector"]
    df["combined_score"] = sector_rank(
        df.set_index("ticker")["combined_raw"],
        sectors,
        min_sector_size=min_sector,
    ).values

    df["direction"] = "NEUTRAL"
    df.loc[df["combined_score"] >= long_thresh, "direction"] = "LONG"
    df.loc[df["combined_score"] <= short_thresh, "direction"] = "SHORT"

    _persist_combined(conn, df, score_date)
    return df


def _load_quant_scores(conn, score_date: str) -> pd.DataFrame:
    rows = conn.execute(
        sa.select(
            factor_scores_table.c.ticker,
            factor_scores_table.c.composite_score,
            factor_scores_table.c.sector,
        ).where(factor_scores_table.c.score_date == score_date)
    ).fetchall()
    return pd.DataFrame(rows, columns=["ticker", "composite_score", "sector"])


def _load_ai_scores(conn, score_date: str) -> pd.DataFrame:
    rows = conn.execute(
        sa.select(
            ai_scores_table.c.ticker,
            ai_scores_table.c.ai_composite,
            ai_scores_table.c.analyzers_used,
        ).where(ai_scores_table.c.score_date == score_date)
    ).fetchall()
    return pd.DataFrame(rows, columns=["ticker", "ai_composite", "analyzers_used"])


def _persist_combined(conn, df: pd.DataFrame, score_date: str) -> None:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    stmt = insert_or_replace(conn, combined_scores_table)
    records = []
    for _, row in df.iterrows():
        records.append(
            {
                "ticker": row["ticker"],
                "score_date": score_date,
                "quant_composite": row.get("composite_score"),
                "ai_composite": row.get("ai_composite"),
                "combined_score": row["combined_score"],
                "direction": row["direction"],
                "computed_at": now,
            }
        )
    if records:
        conn.execute(stmt, records)
        conn.commit()
    logger.info("Combined: wrote %d rows for %s", len(records), score_date)
