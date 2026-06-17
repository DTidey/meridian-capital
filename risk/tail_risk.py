"""Tail-risk monitor — VIX-based and credit-spread-based gross exposure reduction.

Logic
-----
* STRESS  (VIX >= vix_stress)          → REDUCE_GROSS_50
* CAUTION (VIX >= vix_caution)         → REDUCE_GROSS_20
* CAUTION (credit_spread_z >= sigma)   → REDUCE_GROSS_20
* NORMAL                               → no action

When an action is triggered (and whatif=False), all APPROVED opening trades in
position_approvals are scaled back proportionally.  Results are persisted to
risk_events and risk_log.

FRED API (HY spread series BAMLH0A0HYM2) is attempted first; HYG price-level
fallback is used on any error or when the API key is absent.
"""

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import sqlalchemy as sa

from data.db import daily_prices
from portfolio.db import position_approvals
from risk.db import risk_events, risk_log

logger = logging.getLogger(__name__)

_CLOSING_ACTIONS = {"SELL", "COVER"}
_OPENING_ACTIONS = {"BUY", "SHORT"}
_SHARE_ZERO_THRESHOLD = 1.0
_FRED_SERIES = "BAMLH0A0HYM2"
_FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"
_HYG_TICKER = "HYG"
_VIX_TICKER = "^VIX"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_tail_risk(
    conn: sa.engine.Connection,
    score_date: str,
    config: dict,
    cache_dir: Path,
    whatif: bool = False,
) -> dict:
    """Evaluate VIX and credit-spread regime; apply gross exposure reduction if needed.

    Parameters
    ----------
    conn:
        Active SQLAlchemy connection.
    score_date:
        ISO date string for the current run.
    config:
        Full config dict; risk.tail_risk.{vix_caution, vix_stress,
        credit_spread_sigma, credit_lookback_days} are read.
    cache_dir:
        Directory for caching FRED data parquet files.
    whatif:
        If True, compute signals but do not modify position_approvals or write
        to risk_events / risk_log.

    Returns
    -------
    {
        "tail_risk_state":  str,   # "NORMAL", "CAUTION", or "STRESS"
        "vix":              float,
        "credit_spread_z":  float,
        "actions":          list[str],
    }
    """
    cfg = _load_config(config)

    vix = _get_vix(conn, score_date)
    credit_spread_z = _get_credit_spread_z(conn, score_date, cfg, cache_dir)

    # -----------------------------------------------------------------------
    # Determine state and action
    # -----------------------------------------------------------------------
    if vix >= cfg["vix_stress"]:
        state = "STRESS"
        action = "REDUCE_GROSS_50"
        reduction_pct = 0.50
    elif vix >= cfg["vix_caution"] or credit_spread_z >= cfg["credit_spread_sigma"]:
        state = "CAUTION"
        action = "REDUCE_GROSS_20"
        reduction_pct = 0.20
    else:
        state = "NORMAL"
        action = None
        reduction_pct = 0.0

    actions_taken: list[str] = []

    if action:
        actions_taken.append(action)
        logger.warning(
            "tail_risk: %s triggered (%s) | vix=%.2f credit_z=%.3f reduction=%.0f%%",
            state,
            action,
            vix,
            credit_spread_z,
            reduction_pct * 100,
        )
        if not whatif:
            modified = _apply_reduce_gross(conn, score_date, reduction_pct)
            _log_event(
                conn,
                score_date,
                action,
                state,
                {
                    "vix": round(vix, 4),
                    "credit_spread_z": round(credit_spread_z, 4),
                    "reduction_pct": reduction_pct,
                    "modified_count": modified,
                },
            )
            _log_check(
                conn,
                score_date,
                "tail_risk",
                "TRIGGERED",
                f"{action}: vix={vix:.2f} credit_z={credit_spread_z:.3f}",
            )
    else:
        logger.info(
            "tail_risk: NORMAL | vix=%.2f credit_z=%.3f",
            vix,
            credit_spread_z,
        )
        if not whatif:
            _log_check(
                conn,
                score_date,
                "tail_risk",
                "OK",
                f"NORMAL: vix={vix:.2f} credit_z={credit_spread_z:.3f}",
            )

    return {
        "tail_risk_state": state,
        "vix": round(vix, 4),
        "credit_spread_z": round(credit_spread_z, 4),
        "actions": actions_taken,
    }


# ---------------------------------------------------------------------------
# VIX loader
# ---------------------------------------------------------------------------


def _get_vix(conn: sa.engine.Connection, score_date: str) -> float:
    """Return the most recent VIX close on or before score_date. Returns 0.0 if absent."""
    try:
        row = conn.execute(
            sa.select(daily_prices.c.close)
            .where((daily_prices.c.ticker == _VIX_TICKER) & (daily_prices.c.date <= score_date))
            .order_by(daily_prices.c.date.desc())
            .limit(1)
        ).fetchone()

        if row and row[0] is not None:
            return float(row[0])
    except Exception:
        logger.exception("tail_risk: error loading VIX from DB")

    return 0.0


# ---------------------------------------------------------------------------
# Credit spread loader
# ---------------------------------------------------------------------------


def _get_credit_spread_z(
    conn: sa.engine.Connection,
    score_date: str,
    cfg: dict,
    cache_dir: Path,
) -> float:
    """Return z-score of the current HY credit spread vs its historical mean/std.

    Positive z means spreads are wide (risk-off).  Returns 0.0 on error.

    Tries FRED API first (if FRED_API_KEY env var is set); falls back to HYG
    price history from daily_prices.
    """
    fred_key = os.environ.get("FRED_API_KEY", "").strip()
    if fred_key:
        try:
            return _get_credit_spread_z_fred(score_date, fred_key, cfg, cache_dir)
        except Exception:
            logger.exception("tail_risk: FRED API failed — falling back to HYG price method")

    try:
        return _get_credit_spread_z_hyg(conn, score_date, cfg)
    except Exception:
        logger.exception("tail_risk: HYG fallback failed")
    return 0.0


def _get_credit_spread_z_fred(
    score_date: str,
    fred_key: str,
    cfg: dict,
    cache_dir: Path,
) -> float:
    """Fetch BAMLH0A0HYM2 from FRED and return z-score of the latest observation."""
    import requests  # noqa: PLC0415

    lookback = cfg["credit_lookback_days"]
    start_date_dt = datetime.fromisoformat(score_date) - timedelta(days=lookback + 30)
    start_str = start_date_dt.strftime("%Y-%m-%d")

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / "fred_hy_spread.parquet"

    params = {
        "series_id": _FRED_SERIES,
        "observation_start": start_str,
        "api_key": fred_key,
        "file_type": "json",
    }
    response = requests.get(_FRED_BASE_URL, params=params, timeout=15)
    response.raise_for_status()

    payload = response.json()
    observations = payload.get("observations", [])
    if not observations:
        raise ValueError("FRED returned empty observations list")

    rows = []
    for obs in observations:
        d = obs.get("date")
        v = obs.get("value")
        try:
            rows.append({"date": d, "spread": float(v)})
        except (TypeError, ValueError):
            continue  # skip missing/non-numeric values (e.g. ".")

    if not rows:
        raise ValueError("FRED observations had no numeric values")

    fred_df = pd.DataFrame(rows).sort_values("date")
    fred_df = fred_df[fred_df["date"] <= score_date].tail(lookback)

    # Cache for debugging / auditing
    try:
        fred_df.to_parquet(cache_path, index=False)
    except Exception:
        logger.debug("tail_risk: could not write FRED cache to %s", cache_path)

    if len(fred_df) < 10:
        raise ValueError(f"Insufficient FRED observations: {len(fred_df)}")

    latest = float(fred_df["spread"].iloc[-1])
    hist_arr = fred_df["spread"].values.astype(float)
    mean_val = float(np.mean(hist_arr))
    std_val = float(np.std(hist_arr, ddof=1))

    if std_val < 1e-9:
        return 0.0

    return float((latest - mean_val) / std_val)


def _get_credit_spread_z_hyg(
    conn: sa.engine.Connection,
    score_date: str,
    cfg: dict,
) -> float:
    """Fallback: use HYG close prices as a credit-spread proxy.

    HYG price falling => spreads widening, so we invert the return sign.
    z = (today_close - mean_close) / std_close * -1
    """
    lookback = cfg["credit_lookback_days"]

    rows = conn.execute(
        sa.select(
            daily_prices.c.date,
            daily_prices.c.close,
        )
        .where((daily_prices.c.ticker == _HYG_TICKER) & (daily_prices.c.date <= score_date))
        .order_by(daily_prices.c.date.desc())
        .limit(lookback)
    ).fetchall()

    if not rows:
        logger.warning("tail_risk: no HYG price data available")
        return 0.0

    df = pd.DataFrame(rows, columns=["date", "close"]).sort_values("date")
    closes = df["close"].dropna().astype(float)

    if len(closes) < 10:
        logger.warning("tail_risk: insufficient HYG history (%d rows)", len(closes))
        return 0.0

    today_close = float(closes.iloc[-1])
    mean_close = float(closes.mean())
    std_close = float(closes.std(ddof=1))

    if std_close < 1e-9:
        return 0.0

    # Invert: low price = high spread = high z
    return float((today_close - mean_close) / std_close * -1)


# ---------------------------------------------------------------------------
# Gross reduction action
# ---------------------------------------------------------------------------


def _is_closing(action: str, target_shares: float) -> bool:
    """Return True for closing trades (SELL/COVER or near-zero shares)."""
    return action in _CLOSING_ACTIONS or abs(target_shares) < _SHARE_ZERO_THRESHOLD


def _apply_reduce_gross(
    conn: sa.engine.Connection,
    score_date: str,
    reduction_pct: float,
) -> int:
    """Scale target_shares of APPROVED opening trades by (1 - reduction_pct).

    Only BUY / SHORT actions (opening trades) are modified.
    Returns the count of rows modified.
    """
    rows = conn.execute(
        sa.select(position_approvals).where(
            (position_approvals.c.rebalance_date == score_date)
            & (position_approvals.c.status == "APPROVED")
        )
    ).fetchall()

    if not rows:
        return 0

    cols = [c.name for c in position_approvals.columns]
    now = datetime.now(UTC).isoformat(timespec="seconds")
    scale = 1.0 - reduction_pct
    count = 0

    for row in rows:
        d = dict(zip(cols, row, strict=False))
        action = str(d.get("action", "") or "")
        target_shares = float(d.get("target_shares", 0.0) or 0.0)

        if _is_closing(action, target_shares):
            continue
        if action not in _OPENING_ACTIONS:
            continue

        current_shares = float(d.get("current_shares", 0.0) or 0.0)
        new_target = round(target_shares * scale)
        new_delta = new_target - current_shares

        conn.execute(
            position_approvals.update()
            .where(position_approvals.c.id == d["id"])
            .values(
                target_shares=new_target,
                delta_shares=new_delta,
                reviewed_at=now,
            )
        )
        count += 1

    conn.commit()
    logger.info(
        "tail_risk: reduce_gross (pct=%.0f%%) modified %d trades",
        reduction_pct * 100,
        count,
    )
    return count


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------


def _log_event(
    conn: sa.engine.Connection,
    event_date: str,
    event_type: str,
    trigger: str,
    detail_dict: dict,
) -> None:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    conn.execute(
        risk_events.insert().values(
            event_date=event_date,
            event_type=event_type,
            trigger=trigger,
            detail=json.dumps(detail_dict),
            recorded_at=now,
        )
    )
    conn.commit()


def _log_check(
    conn: sa.engine.Connection,
    run_date: str,
    check_type: str,
    result: str,
    reason: str,
) -> None:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    conn.execute(
        risk_log.insert().values(
            run_date=run_date,
            check_type=check_type,
            ticker=None,
            result=result,
            reason=reason,
            recorded_at=now,
        )
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------


def _load_config(config: dict) -> dict:
    tr = config.get("risk", {}).get("tail_risk", {})
    return {
        "vix_caution": float(tr.get("vix_caution", 25)),
        "vix_stress": float(tr.get("vix_stress", 35)),
        "credit_spread_sigma": float(tr.get("credit_spread_sigma", 1.0)),
        "credit_lookback_days": int(tr.get("credit_lookback_days", 252)),
    }
