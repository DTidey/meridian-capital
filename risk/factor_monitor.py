"""Factor spread monitor — z-scores each long-minus-short factor spread vs historical
cross-sectional distribution.  Alerts when |z| > threshold.

Logic
-----
1. Load today's universe factor scores and compute the portfolio spread via
   compute_exposures().
2. Load the last 252 score_dates <= score_date and derive a historical factor spread
   distribution: for each historical date compute (mean top-half – mean bottom-half)
   per factor across the full universe.
3. Z-score today's spread against that distribution.
4. Load crowding_flags for score_date; promote any alert whose factor appears in a
   flagged pair to HIGH priority.
5. Log to risk_log and return alerts list.
"""

import logging

import numpy as np
import pandas as pd
import sqlalchemy as sa
from datetime import datetime, timezone

from factors.db import factor_scores as factor_scores_table, crowding_flags
from portfolio.db import portfolio_positions
from portfolio.factor_exposure import compute_exposures
from risk.db import risk_log

logger = logging.getLogger(__name__)

_FACTOR_COLS = [
    "momentum_score", "quality_score", "value_score", "revisions_score",
    "insider_score", "growth_score", "short_interest_score", "institutional_score",
]

_MIN_HIST_DATES = 10   # minimum dates required to produce a z-score
_MIN_TICKERS_PER_HALF = 3  # minimum tickers per half to compute a spread


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_factor_monitor(
    conn: sa.engine.Connection,
    positions_df: pd.DataFrame,
    score_date: str,
    config: dict,
    whatif: bool = False,
) -> list[dict]:
    """Z-score each factor spread and return a list of alert dicts.

    Parameters
    ----------
    conn:
        Active SQLAlchemy connection.
    positions_df:
        Current portfolio positions (ticker, direction, weight columns required).
    score_date:
        ISO date string for the current score date.
    config:
        Full config dict; risk.factor_monitor.alert_z_threshold used.
    whatif:
        If True, do not write to DB.

    Returns
    -------
    list of dicts: [{"type": "FACTOR_SPREAD", "factor": str, "z": float,
                     "priority": "HIGH"|"MEDIUM"}]
    """
    threshold = float(
        config.get("risk", {})
              .get("factor_monitor", {})
              .get("alert_z_threshold", 1.5)
    )

    # -----------------------------------------------------------------------
    # Step 1 — today's factor scores and portfolio spread
    # -----------------------------------------------------------------------
    factor_scores_df = _load_scores_for_date(conn, score_date)

    if factor_scores_df.empty:
        logger.warning("factor_monitor: no factor scores for %s — skipping", score_date)
        if not whatif:
            _log_check(conn, score_date, "factor_monitor", None, "OK",
                       f"no factor scores for {score_date}")
        return []

    exposures = compute_exposures(positions_df, factor_scores_df)
    today_spread: dict[str, float] = exposures.get("spread", {})

    if not today_spread:
        logger.warning("factor_monitor: empty spread returned by compute_exposures")
        if not whatif:
            _log_check(conn, score_date, "factor_monitor", None, "OK",
                       "empty spread — no positions")
        return []

    # -----------------------------------------------------------------------
    # Step 2 — historical spreads (last 252 score dates)
    # -----------------------------------------------------------------------
    hist_dates = _load_252_score_dates(conn, score_date)

    if not hist_dates:
        logger.warning("factor_monitor: no historical score dates found — cannot z-score")
        if not whatif:
            _log_check(conn, score_date, "factor_monitor", None, "OK",
                       "insufficient history for z-scoring")
        return []

    hist_spreads: dict[str, list[float]] = {f: [] for f in _FACTOR_COLS}

    for hdate in hist_dates:
        hscores = _load_scores_for_date(conn, hdate)
        if hscores.empty:
            continue
        date_spreads = _compute_universe_spread(hscores)
        for factor, val in date_spreads.items():
            if val is not None:
                hist_spreads[factor].append(val)

    # -----------------------------------------------------------------------
    # Step 3 — z-score today's spread
    # -----------------------------------------------------------------------
    factor_z: dict[str, float] = {}
    for factor in _FACTOR_COLS:
        if factor not in today_spread:
            continue
        history = hist_spreads.get(factor, [])
        if len(history) < _MIN_HIST_DATES:
            logger.debug(
                "factor_monitor: insufficient history for %s (%d dates)", factor, len(history)
            )
            continue
        hist_arr = np.array(history, dtype=float)
        hist_mean = float(np.mean(hist_arr))
        hist_std = float(np.std(hist_arr, ddof=1))
        if hist_std < 1e-9:
            continue
        z = (today_spread[factor] - hist_mean) / hist_std
        factor_z[factor] = z

    # -----------------------------------------------------------------------
    # Step 4 — crowding flags
    # -----------------------------------------------------------------------
    crowded_factors = _load_crowded_factors(conn, score_date)

    # -----------------------------------------------------------------------
    # Step 5 — build alerts
    # -----------------------------------------------------------------------
    alerts: list[dict] = []
    for factor, z_val in factor_z.items():
        if abs(z_val) > threshold:
            priority = "HIGH" if factor in crowded_factors else "MEDIUM"
            alerts.append({
                "type":     "FACTOR_SPREAD",
                "factor":   factor,
                "z":        round(z_val, 4),
                "priority": priority,
            })
            logger.warning(
                "factor_monitor: ALERT %s z=%.3f priority=%s (threshold=%.2f)",
                factor, z_val, priority, threshold,
            )

    # -----------------------------------------------------------------------
    # Step 6 — log to risk_log
    # -----------------------------------------------------------------------
    if not whatif:
        if alerts:
            for alert in alerts:
                _log_check(
                    conn, score_date, "factor_monitor", None, "WARNING",
                    f"FACTOR_SPREAD {alert['factor']} z={alert['z']:.4f} priority={alert['priority']}",
                )
        else:
            _log_check(
                conn, score_date, "factor_monitor", None, "OK",
                f"all factor spreads within threshold={threshold}",
            )

    logger.info(
        "factor_monitor: %s | %d alerts (threshold=%.2f)",
        score_date, len(alerts), threshold,
    )
    return alerts


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_scores_for_date(
    conn: sa.engine.Connection,
    score_date: str,
) -> pd.DataFrame:
    """Return a DataFrame of factor scores for the given score_date."""
    available_cols = [col.name for col in factor_scores_table.columns]
    select_cols = [factor_scores_table.c.ticker]
    for col in _FACTOR_COLS:
        if col in available_cols:
            select_cols.append(factor_scores_table.c[col])

    rows = conn.execute(
        sa.select(*select_cols).where(factor_scores_table.c.score_date == score_date)
    ).fetchall()

    if not rows:
        return pd.DataFrame()

    col_names = ["ticker"] + [col.name for col in select_cols[1:]]
    return pd.DataFrame(rows, columns=col_names)


def _load_252_score_dates(
    conn: sa.engine.Connection,
    score_date: str,
) -> list[str]:
    """Return up to 252 distinct score_dates strictly before score_date, descending."""
    rows = conn.execute(
        sa.select(factor_scores_table.c.score_date)
        .where(factor_scores_table.c.score_date < score_date)
        .distinct()
        .order_by(factor_scores_table.c.score_date.desc())
        .limit(252)
    ).fetchall()
    return [r[0] for r in rows]


def _compute_universe_spread(scores_df: pd.DataFrame) -> dict[str, float | None]:
    """For each factor, compute mean(top-half) - mean(bottom-half) across tickers."""
    result: dict[str, float | None] = {}
    n = len(scores_df)
    if n < _MIN_TICKERS_PER_HALF * 2:
        return {f: None for f in _FACTOR_COLS}

    half = n // 2
    for factor in _FACTOR_COLS:
        if factor not in scores_df.columns:
            result[factor] = None
            continue
        vals = scores_df[factor].dropna().sort_values()
        if len(vals) < _MIN_TICKERS_PER_HALF * 2:
            result[factor] = None
            continue
        h = len(vals) // 2
        top_mean = float(vals.iloc[h:].mean())
        bot_mean = float(vals.iloc[:h].mean())
        result[factor] = top_mean - bot_mean
    return result


def _load_crowded_factors(
    conn: sa.engine.Connection,
    score_date: str,
) -> set[str]:
    """Return the set of factor names that appear in a flagged crowding pair."""
    available_cols = [col.name for col in crowding_flags.columns]
    if "flagged" not in available_cols:
        return set()

    rows = conn.execute(
        sa.select(
            crowding_flags.c.factor_a,
            crowding_flags.c.factor_b,
        ).where(
            (crowding_flags.c.score_date == score_date) &
            (crowding_flags.c.flagged == 1)
        )
    ).fetchall()

    crowded: set[str] = set()
    for row in rows:
        crowded.add(row[0])
        crowded.add(row[1])
    return crowded


def _log_check(
    conn: sa.engine.Connection,
    run_date: str,
    check_type: str,
    ticker: str | None,
    result: str,
    reason: str,
) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        risk_log.insert().values(
            run_date=run_date,
            check_type=check_type,
            ticker=ticker,
            result=result,
            reason=reason,
            recorded_at=now,
        )
    )
    conn.commit()
