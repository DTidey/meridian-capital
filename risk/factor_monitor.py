"""Factor spread monitor — z-scores each long-minus-short factor spread vs the book's own
past spreads.  Alerts when |z| > threshold.

Logic
-----
1. Skip when the book has no LONG or no SHORT positions. Otherwise load today's factor
   scores and compute the portfolio spread via compute_exposures().
2. For each of the last 252 portfolio_history snapshot dates before score_date, compute
   that day's book spread the same way, against that day's factor scores.
3. Z-score today's spread against that distribution (needs at least 10 dates).
4. Load crowding_flags for score_date; promote any alert whose factor appears in a
   flagged pair to HIGH priority.
5. Log to risk_log and return alerts list.
"""

import logging
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import sqlalchemy as sa

from factors.db import crowding_flags
from factors.db import factor_scores as factor_scores_table
from portfolio.db import portfolio_history
from portfolio.factor_exposure import compute_exposures
from risk.db import risk_log

logger = logging.getLogger(__name__)

_FACTOR_COLS = [
    "momentum_score",
    "quality_score",
    "value_score",
    "revisions_score",
    "insider_score",
    "growth_score",
    "short_interest_score",
    "institutional_score",
]

_MIN_HIST_DATES = 10  # minimum dates required to produce a z-score
_MAX_HIST_DATES = 252  # snapshot dates in the baseline


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
        config.get("risk", {}).get("factor_monitor", {}).get("alert_z_threshold", 1.5)
    )

    # -----------------------------------------------------------------------
    # Step 1 — today's factor scores and portfolio spread
    # -----------------------------------------------------------------------
    if not _has_both_books(positions_df):
        logger.info("factor_monitor: no positions — skipped")
        if not whatif:
            _log_check(conn, score_date, "factor_monitor", None, "OK", "no positions")
        return []

    factor_scores_df = _load_scores_for_date(conn, score_date)

    if factor_scores_df.empty:
        logger.warning("factor_monitor: no factor scores for %s — skipping", score_date)
        if not whatif:
            _log_check(
                conn, score_date, "factor_monitor", None, "OK", f"no factor scores for {score_date}"
            )
        return []

    exposures = compute_exposures(positions_df, factor_scores_df)
    today_spread: dict[str, float] = exposures["spread"]

    # -----------------------------------------------------------------------
    # Step 2 — historical portfolio spreads (last 252 snapshot dates)
    # -----------------------------------------------------------------------
    hist_spreads = _load_history_spreads(conn, score_date)
    n_dates = len(hist_spreads[_FACTOR_COLS[0]])

    if n_dates < _MIN_HIST_DATES:
        reason = f"insufficient history for z-scoring ({n_dates} snapshot dates)"
        logger.info("factor_monitor: %s — skipped", reason)
        if not whatif:
            _log_check(conn, score_date, "factor_monitor", None, "OK", reason)
        return []

    # -----------------------------------------------------------------------
    # Step 3 — z-score today's spread
    # -----------------------------------------------------------------------
    factor_z: dict[str, float] = {}
    for factor in _FACTOR_COLS:
        history = hist_spreads[factor]
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
            alerts.append(
                {
                    "type": "FACTOR_SPREAD",
                    "factor": factor,
                    "z": round(z_val, 4),
                    "priority": priority,
                }
            )
            logger.warning(
                "factor_monitor: ALERT %s z=%.3f priority=%s (threshold=%.2f)",
                factor,
                z_val,
                priority,
                threshold,
            )

    # -----------------------------------------------------------------------
    # Step 6 — log to risk_log
    # -----------------------------------------------------------------------
    if not whatif:
        if alerts:
            for alert in alerts:
                _log_check(
                    conn,
                    score_date,
                    "factor_monitor",
                    None,
                    "WARNING",
                    f"FACTOR_SPREAD {alert['factor']} z={alert['z']:.4f} priority={alert['priority']}",
                )
        else:
            _log_check(
                conn,
                score_date,
                "factor_monitor",
                None,
                "OK",
                f"all factor spreads within threshold={threshold}",
            )

    logger.info(
        "factor_monitor: %s | %d alerts (threshold=%.2f)",
        score_date,
        len(alerts),
        threshold,
    )
    return alerts


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _has_both_books(positions_df: pd.DataFrame) -> bool:
    """True when the book holds at least one LONG and one SHORT position."""
    if "direction" not in positions_df.columns:
        return False
    directions = set(positions_df["direction"])
    return {"LONG", "SHORT"} <= directions


def _load_scores_for_date(
    conn: sa.engine.Connection,
    score_date: str,
) -> pd.DataFrame:
    """Return a DataFrame of factor scores for the given score_date."""
    return _load_scores_for_dates(conn, [score_date])


def _load_scores_for_dates(
    conn: sa.engine.Connection,
    score_dates: list[str],
) -> pd.DataFrame:
    """Return ticker, score_date and factor scores for the given score_dates."""
    available_cols = [col.name for col in factor_scores_table.columns]
    select_cols = [factor_scores_table.c.ticker, factor_scores_table.c.score_date]
    for col in _FACTOR_COLS:
        if col in available_cols:
            select_cols.append(factor_scores_table.c[col])

    rows = conn.execute(
        sa.select(*select_cols).where(factor_scores_table.c.score_date.in_(score_dates))
    ).fetchall()
    return pd.DataFrame(rows, columns=[col.name for col in select_cols])


def _load_history_spreads(
    conn: sa.engine.Connection,
    score_date: str,
) -> dict[str, list[float]]:
    """Past portfolio spreads per factor, computed the same way as today's spread.

    One value per snapshot date (last 252 before score_date) that has both books and
    factor scores for that date.
    """
    hist: dict[str, list[float]] = {f: [] for f in _FACTOR_COLS}
    dates = _load_snapshot_dates(conn, score_date)
    if not dates:
        return hist

    snapshots = _load_snapshots(conn, dates)
    scores_by_date = dict(tuple(_load_scores_for_dates(conn, dates).groupby("score_date")))
    for snap_date, book in snapshots.groupby("snapshot_date"):
        day_scores = scores_by_date.get(snap_date)
        if day_scores is None or not _has_both_books(book):
            continue
        spread = compute_exposures(book, day_scores)["spread"]
        for factor in _FACTOR_COLS:
            hist[factor].append(spread[factor])
    return hist


def _load_snapshot_dates(
    conn: sa.engine.Connection,
    score_date: str,
) -> list[str]:
    """Return up to 252 distinct portfolio snapshot dates strictly before score_date."""
    rows = conn.execute(
        sa.select(portfolio_history.c.snapshot_date)
        .where(portfolio_history.c.snapshot_date < score_date)
        .distinct()
        .order_by(portfolio_history.c.snapshot_date.desc())
        .limit(_MAX_HIST_DATES)
    ).fetchall()
    return [r[0] for r in rows]


def _load_snapshots(
    conn: sa.engine.Connection,
    dates: list[str],
) -> pd.DataFrame:
    """Return snapshot rows for the dates, keeping only each date's last commit."""
    cols = ["snapshot_date", "ticker", "direction", "weight", "recorded_at"]
    rows = conn.execute(
        sa.select(*[portfolio_history.c[c] for c in cols]).where(
            portfolio_history.c.snapshot_date.in_(dates)
        )
    ).fetchall()
    df = pd.DataFrame(rows, columns=cols)
    # an all-null weight column loads as object dtype, which abs() rejects
    df["weight"] = pd.to_numeric(df["weight"])
    # Layer 4 can commit twice in a day; the last commit is the book that stood, and
    # tickers closed between commits must not carry over from the earlier one
    stamp = df["recorded_at"].fillna("")
    return df[stamp == stamp.groupby(df["snapshot_date"]).transform("max")]


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
        ).where((crowding_flags.c.score_date == score_date) & (crowding_flags.c.flagged == 1))
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
    now = datetime.now(UTC).isoformat(timespec="seconds")
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
