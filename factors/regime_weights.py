"""Regime-conditional factor weights: a linear blend across VIX bands (packet 64).

The blend is driven by the mean of the last `vix_smoothing_days` VIX closes, so it is a pure
function of stored closes: a rerun or backfill of a score date gives the same weights.
"""

import logging
from datetime import date, timedelta

import pandas as pd

logger = logging.getLogger(__name__)

_REGIME_LOW_VOL = "LOW_VOL"
_REGIME_NORMAL = "NORMAL"
_REGIME_HIGH_VOL = "HIGH_VOL"

_DEFAULT_DAYS = 5
_DEFAULT_LOW = {"vix_full": 12.0, "vix_none": 18.0}
_DEFAULT_HIGH = {"vix_none": 20.0, "vix_full": 30.0}
_MIN_WINDOW_DAYS = 14  # calendar days searched for closes (holidays, missed ingests)


def smoothing_days(regime_config: dict) -> int:
    """`vix_smoothing_days`, validated (integer >= 1)."""
    days = regime_config.get("vix_smoothing_days", _DEFAULT_DAYS)
    if isinstance(days, bool) or not isinstance(days, int) or days < 1:
        raise ValueError(
            f"scoring.regime_weights.vix_smoothing_days must be an integer >= 1, got {days!r}"
        )
    return days


def window_days(days: int) -> int:
    """Calendar days searched for the last `days` closes: 14, or 2 x days when larger."""
    return max(_MIN_WINDOW_DAYS, 2 * days)


def band_edges(regime_config: dict) -> tuple[float, float, float, float]:
    """(low full, low none, high none, high full), validated as strictly ordered bands."""
    low = {**_DEFAULT_LOW, **regime_config.get("low_vol", {})}
    high = {**_DEFAULT_HIGH, **regime_config.get("high_vol", {})}
    edges = (low["vix_full"], low["vix_none"], high["vix_none"], high["vix_full"])
    if not (edges[0] < edges[1] <= edges[2] < edges[3]):
        raise ValueError(
            "scoring.regime_weights band edges must satisfy low_vol.vix_full < "
            "low_vol.vix_none <= high_vol.vix_none < high_vol.vix_full, got "
            f"{edges[0]}, {edges[1]}, {edges[2]}, {edges[3]}"
        )
    return tuple(float(e) for e in edges)


def validate(regime_config: dict) -> None:
    """Raise ValueError naming the key for an invalid regime_weights section."""
    smoothing_days(regime_config)
    band_edges(regime_config)


def smoothed_vix(vix_df: pd.DataFrame, days: int, score_date: str) -> float | None:
    """Mean of the last `days` closes dated on or before score_date, within the window.

    Fewer closes than `days` (but at least one) give their mean with a warning; none gives None.
    """
    if vix_df.empty:
        return None
    end = date.fromisoformat(score_date)
    start = (end - timedelta(days=window_days(days))).isoformat()
    dates = vix_df["date"].astype(str).str[:10]
    in_window = vix_df[(dates > start) & (dates <= score_date) & vix_df["close"].notna()]
    closes = in_window.assign(_d=dates).sort_values("_d", ascending=False)["close"].head(days)
    if closes.empty:
        return None
    if len(closes) < days:
        logger.warning(
            "VIX smoothing: %d of %d closes found in the %d days to %s",
            len(closes),
            days,
            window_days(days),
            score_date,
        )
    return float(closes.astype(float).mean())


def regime_shares(vix_smoothed: float | None, regime_config: dict) -> tuple[float, float]:
    """(high_vol share, low_vol share), each in [0, 1]; (0, 0) when VIX is unknown."""
    if vix_smoothed is None:
        return 0.0, 0.0
    low_full, low_none, high_none, high_full = band_edges(regime_config)
    high = (vix_smoothed - high_none) / (high_full - high_none)
    low = (low_none - vix_smoothed) / (low_none - low_full)
    return min(max(high, 0.0), 1.0), min(max(low, 0.0), 1.0)


def blend_weights(
    base: dict[str, float], regime_config: dict, high_share: float, low_share: float
) -> dict[str, float]:
    """(1 - high - low) x base + high x high_vol weights + low x low_vol weights; sums to 1."""
    high_w = adjust_weights(base, _REGIME_HIGH_VOL, regime_config)
    low_w = adjust_weights(base, _REGIME_LOW_VOL, regime_config)
    base_w = _normalise(dict(base))
    rest = 1.0 - high_share - low_share
    blended = {k: rest * base_w[k] + high_share * high_w[k] + low_share * low_w[k] for k in base_w}
    return _normalise(blended)


def regime_label(high_share: float, low_share: float) -> str:
    """HIGH_VOL / LOW_VOL when that share is >= 0.5, else NORMAL."""
    if high_share >= 0.5:
        return _REGIME_HIGH_VOL
    if low_share >= 0.5:
        return _REGIME_LOW_VOL
    return _REGIME_NORMAL


def resolve(
    vix_df: pd.DataFrame, base: dict[str, float], scoring_cfg: dict, score_date: str
) -> dict:
    """Weights and regime state for a score date: the blend, or base weights when it is off.

    Returns keys: weights, regime, vix_close, vix_smoothed, high_vol_share, low_vol_share.
    """
    regime_config = scoring_cfg.get("regime_weights", {}) or {}
    validate(regime_config)
    days = smoothing_days(regime_config)
    vix_close = smoothed_vix(vix_df, 1, score_date)
    vix_mean = smoothed_vix(vix_df, days, score_date)
    if vix_mean is None:
        logger.warning("VIX data unavailable for %s: base weights, NORMAL regime", score_date)
    if not scoring_cfg.get("regime_conditional_weights", True):
        high, low = 0.0, 0.0
    else:
        high, low = regime_shares(vix_mean, regime_config)
    return {
        "weights": blend_weights(base, regime_config, high, low),
        "regime": regime_label(high, low),
        "vix_close": vix_close,
        "vix_smoothed": vix_mean,
        "high_vol_share": high,
        "low_vol_share": low,
    }


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
