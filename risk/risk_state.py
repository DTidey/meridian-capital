"""Persistent risk state: cache/risk_state.json + cache/halt.lock."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_HALT_LOCK  = "halt.lock"
_STATE_FILE = "risk_state.json"


def default_state() -> dict:
    return {
        "as_of":                 None,
        "nav_usd":               0.0,
        "daily_pnl_usd":         0.0,
        "daily_pnl_pct":         0.0,
        "weekly_pnl_pct":        0.0,
        "peak_nav_usd":          0.0,
        "drawdown_pct":          0.0,
        "halted":                False,
        "circuit_breaker_state": "NORMAL",
        "tail_risk_state":       "NORMAL",
        "gross_exposure":        0.0,
        "net_exposure":          0.0,
        "net_beta":              0.0,
        "factor_exposures": {
            "long":   {},
            "short":  {},
            "spread": {},
            "flags":  [],
        },
        "risk_decomposition": {
            "factor_var_pct":       0.0,
            "specific_var_pct":     0.0,
            "annualised_vol":       0.0,
            "factor_contributions": {},
        },
        "mctr_top5": [],
        "correlation_monitor": {
            "long_avg_corr":    0.0,
            "short_avg_corr":   0.0,
            "effective_n_bets": 0.0,
            "alerts":           [],
        },
        "alerts": [],
    }


def load_risk_state(cache_dir: Path) -> dict:
    path = cache_dir / _STATE_FILE
    if not path.exists():
        logger.debug("Risk state file not found at %s; returning default state", path)
        return default_state()
    with path.open() as fh:
        state = json.load(fh)
    logger.debug("Loaded risk state from %s", path)
    return state


def save_risk_state(state: dict, cache_dir: Path) -> None:
    state["halted"] = is_halted(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / _STATE_FILE
    with path.open("w") as fh:
        json.dump(state, fh, indent=2)


def is_halted(cache_dir: Path) -> bool:
    return (cache_dir / _HALT_LOCK).exists()


def set_halt(cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    lock_path = cache_dir / _HALT_LOCK
    ts = datetime.now(timezone.utc).isoformat()
    lock_path.write_text(ts)
    logger.warning("Trading HALTED — lock written to %s at %s", lock_path, ts)


def clear_halt(cache_dir: Path) -> None:
    lock_path = cache_dir / _HALT_LOCK
    if lock_path.exists():
        lock_path.unlink()
        logger.info("Trading halt cleared — lock removed from %s", lock_path)
