"""Regime-conditional factor weight adjustment based on VIX level."""

import logging

import pandas as pd

logger = logging.getLogger(__name__)

_REGIME_LOW_VOL  = "LOW_VOL"
_REGIME_NORMAL   = "NORMAL"
_REGIME_HIGH_VOL = "HIGH_VOL"


def resolve_regime(vix_df: pd.DataFrame) -> tuple[str, float | None]:
    """Determine market regime from VIX close.

    Returns:
        (regime_str, vix_close) — vix_close is None when no data available.
    """
    if vix_df.empty:
        logger.warning("VIX data unavailable — defaulting to NORMAL regime")
        return _REGIME_NORMAL, None

    vix_close = float(vix_df.iloc[0]["close"])

    if vix_close < 15:
        return _REGIME_LOW_VOL, vix_close
    if vix_close > 25:
        return _REGIME_HIGH_VOL, vix_close
    return _REGIME_NORMAL, vix_close


def adjust_weights(
    base_weights: dict[str, float],
    regime: str,
    regime_config: dict,
) -> dict[str, float]:
    """Return weight dict adjusted for the current regime and re-normalised.

    Args:
        base_weights: Default weights from config (must sum to 1.0).
        regime: One of LOW_VOL / NORMAL / HIGH_VOL.
        regime_config: The scoring.regime_weights section of config.yaml.

    Returns:
        Adjusted weights dict, re-normalised to sum to 1.0.
    """
    weights = dict(base_weights)

    if regime == _REGIME_NORMAL:
        return weights

    if regime == _REGIME_LOW_VOL:
        overrides = regime_config.get("low_vol", {})
        if "momentum" in overrides:
            weights["momentum"] = overrides["momentum"]
        if "value" in overrides:
            weights["value"] = overrides["value"]

    elif regime == _REGIME_HIGH_VOL:
        overrides = regime_config.get("high_vol", {})
        if "quality" in overrides:
            weights["quality"] = overrides["quality"]
        if "value" in overrides:
            weights["value"] = overrides["value"]
        if "momentum" in overrides:
            weights["momentum"] = overrides["momentum"]

    return _normalise(weights)


def _normalise(weights: dict[str, float]) -> dict[str, float]:
    """Re-normalise weights so they sum exactly to 1.0."""
    total = sum(weights.values())
    if total == 0:
        raise ValueError("Total weight is zero — cannot normalise")
    return {k: v / total for k, v in weights.items()}
