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
    """Blend Layer 2 and Layer 3 scores for the Layer 2 candidates and write to DB.

    Direction is the Layer 2 direction: only the LONG/SHORT candidates, which Layer 3
    reviews, can be LONG/SHORT. Within each side, quant and AI strength ranks are blended
    and mapped onto the side's band (LONG long_thresh-100, SHORT 0-short_thresh). NEUTRAL
    names keep their quant composite.

    Returns a DataFrame with columns: ticker, sector, composite_score,
    ai_composite, analyzers_used, combined_score, direction.
    """
    scoring_cfg = config.get("scoring", {})
    combined_cfg = config.get("analysis", {}).get("combined_score", {})
    quant_weight = float(combined_cfg.get("quant_weight", 0.60))
    ai_weight = float(combined_cfg.get("ai_weight", 0.40))
    long_thresh = scoring_cfg.get("long_quintile_threshold", 80)
    short_thresh = scoring_cfg.get("short_quintile_threshold", 20)

    quant_df = _load_quant_scores(conn, score_date)
    ai_df = _load_ai_scores(conn, score_date)

    if quant_df.empty:
        logger.warning("Combined: no quant scores for %s", score_date)
        return pd.DataFrame()

    df = quant_df.merge(ai_df, on="ticker", how="left")
    df["analyzers_used"] = df["analyzers_used"].fillna(0).astype(int)
    df["direction"] = df["direction"].fillna("NEUTRAL")
    df["combined_score"] = df["composite_score"]

    weights = (quant_weight, ai_weight)
    long_mask = df["direction"] == "LONG"
    short_mask = df["direction"] == "SHORT"
    blend = _side_blend(df[long_mask], 1, weights)
    df.loc[long_mask, "combined_score"] = long_thresh + (100 - long_thresh) * blend / 100
    blend = _side_blend(df[short_mask], -1, weights)
    df.loc[short_mask, "combined_score"] = short_thresh - short_thresh * blend / 100

    _persist_combined(conn, df, score_date)
    return df


def _side_blend(side_df: pd.DataFrame, sign: int, weights: tuple[float, float]) -> pd.Series:
    """0-100 blend of quant and AI strength ranks within one side; sign -1 for SHORT."""
    quant_weight, ai_weight = weights
    ai = side_df["ai_composite"].where(side_df["analyzers_used"] > 0)
    quant_rank = _strength_rank(sign * side_df["composite_score"])
    ai_rank = _strength_rank(sign * ai).fillna(50.0)
    return (quant_weight * quant_rank + ai_weight * ai_rank) / (quant_weight + ai_weight)


def _strength_rank(values: pd.Series) -> pd.Series:
    """Mid-rank percentile, 0-100 with higher values stronger; NaN stays NaN."""
    return (values.rank(method="average") - 0.5) / values.notna().sum() * 100


def _load_quant_scores(conn, score_date: str) -> pd.DataFrame:
    rows = conn.execute(
        sa.select(
            factor_scores_table.c.ticker,
            factor_scores_table.c.composite_score,
            factor_scores_table.c.sector,
            factor_scores_table.c.direction,
        ).where(factor_scores_table.c.score_date == score_date)
    ).fetchall()
    return pd.DataFrame(rows, columns=["ticker", "composite_score", "sector", "direction"])


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
