"""Composite score: weighted blend of 8 factor scores + LONG/SHORT labelling."""

import logging

import pandas as pd

from factors._utils import sector_rank

logger = logging.getLogger(__name__)

_FACTOR_COLS = [
    ("momentum",       "momentum_score"),
    ("quality",        "quality_score"),
    ("value",          "value_score"),
    ("revisions",      "revisions_score"),
    ("insider",        "insider_score"),
    ("growth",         "growth_score"),
    ("short_interest", "short_interest_score"),
    ("institutional",  "institutional_score"),
]


def compute(
    factor_scores: dict[str, pd.DataFrame],
    universe: pd.DataFrame,
    weights: dict[str, float],
    config: dict,
) -> pd.DataFrame:
    """Blend factor scores into a composite and label LONG/SHORT.

    Args:
        factor_scores: Dict mapping factor name → scored DataFrame (index = ticker).
        universe: Universe DataFrame with 'ticker' and 'sector' columns.
        weights: Dict mapping factor name → weight (must sum to 1.0).
        config: Parsed config.yaml (scoring section used for thresholds).

    Returns:
        DataFrame indexed by ticker with all factor score columns plus
        composite_score and direction.
    """
    scoring_cfg  = config.get("scoring", {})
    long_thresh  = scoring_cfg.get("long_quintile_threshold", 80)
    short_thresh = scoring_cfg.get("short_quintile_threshold", 20)
    min_size     = scoring_cfg.get("min_sector_size", 5)

    _validate_weights(weights)

    sectors = universe.set_index("ticker")["sector"]
    tickers = universe["ticker"].tolist()

    # Build unified DataFrame: one row per ticker, all factor score columns
    combined = pd.DataFrame(index=tickers)
    combined["sector"] = sectors.reindex(tickers)

    for factor_name, score_col in _FACTOR_COLS:
        df = factor_scores.get(factor_name)
        if df is not None and score_col in df.columns:
            combined[score_col] = df[score_col].reindex(tickers)
        else:
            logger.warning("Composite: missing factor '%s' — substituting 50.0", factor_name)
            combined[score_col] = 50.0

    # Also copy sub-factor columns from each factor DataFrame
    for factor_name, _score_col in _FACTOR_COLS:
        df = factor_scores.get(factor_name)
        if df is not None:
            sub_cols = [c for c in df.columns if c != _score_col]
            for col in sub_cols:
                combined[col] = df[col].reindex(tickers)

    # SHORT composite: flip short_interest_score (LONG convention → SHORT convention)
    si_long_score = combined["short_interest_score"].copy()
    si_short_score = 100.0 - si_long_score

    # Weighted composite (using LONG convention for all factors)
    raw_composite = pd.Series(0.0, index=tickers)
    for factor_name, score_col in _FACTOR_COLS:
        weight = weights.get(factor_name, 0.0)
        raw_composite += weight * combined[score_col].fillna(50.0)

    combined["composite_score"] = sector_rank(
        raw_composite, sectors.reindex(tickers), min_size
    )

    # LONG/SHORT labels based on composite
    def _label(score):
        if score >= long_thresh:
            return "LONG"
        if score <= short_thresh:
            return "SHORT"
        return "NEUTRAL"

    combined["direction"] = combined["composite_score"].map(_label)

    return combined


def _validate_weights(weights: dict[str, float]) -> None:
    total = sum(weights.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"Factor weights must sum to 1.0, got {total:.6f}")
