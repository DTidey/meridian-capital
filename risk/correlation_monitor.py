"""Correlation monitor — computes 60-day rolling pairwise correlations within each
book (long / short) and reports the effective number of independent bets.

Logic
-----
1. Split positions into LONG and SHORT books.
2. Load adj_close for all tickers over the lookback window.
3. Compute log returns; drop tickers with fewer than 20 observations.
4. Compute average pairwise correlation per book.
5. Compute the effective number of bets (Herfindahl / entropy of eigenvalues) for
   the combined portfolio.
6. Alert if either book's average correlation exceeds the configured threshold.
7. Log to risk_log and return result dict.
"""

import logging

import numpy as np
import pandas as pd
import sqlalchemy as sa
from datetime import datetime, timezone

from data.db import daily_prices
from risk.db import risk_log

logger = logging.getLogger(__name__)

_MIN_RETURNS = 20   # minimum number of return observations required per ticker


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_correlation_monitor(
    conn: sa.engine.Connection,
    positions_df: pd.DataFrame,
    score_date: str,
    config: dict,
    whatif: bool = False,
) -> dict:
    """Compute intra-book correlations and effective number of bets.

    Parameters
    ----------
    conn:
        Active SQLAlchemy connection.
    positions_df:
        Current portfolio positions (ticker, direction columns required).
    score_date:
        ISO date string used as the upper bound for price history.
    config:
        Full config dict; risk.correlation_monitor.{alert_avg_corr, lookback_days}.
    whatif:
        If True, do not write to DB.

    Returns
    -------
    {
        "long_avg_corr":    float,
        "short_avg_corr":   float,
        "effective_n_bets": float,
        "alerts":           list[dict],
    }
    """
    cfg_section = config.get("risk", {}).get("correlation_monitor", {})
    alert_avg_corr = float(cfg_section.get("alert_avg_corr", 0.60))
    lookback_days  = int(cfg_section.get("lookback_days",  60))

    # -----------------------------------------------------------------------
    # Step 1 — split books
    # -----------------------------------------------------------------------
    long_tickers  = _book_tickers(positions_df, "LONG")
    short_tickers = _book_tickers(positions_df, "SHORT")
    all_tickers   = list(set(long_tickers) | set(short_tickers))

    if not all_tickers:
        logger.info("correlation_monitor: no positions found for %s", score_date)
        result = {
            "long_avg_corr":    0.0,
            "short_avg_corr":   0.0,
            "effective_n_bets": 0.0,
            "alerts":           [],
        }
        if not whatif:
            _log_check(conn, score_date, "correlation_monitor", "OK",
                       "no positions in portfolio")
        return result

    # -----------------------------------------------------------------------
    # Step 2 — load price history
    # -----------------------------------------------------------------------
    returns_df = _load_returns(conn, all_tickers, score_date, lookback_days)

    # -----------------------------------------------------------------------
    # Steps 3-5 — correlation stats
    # -----------------------------------------------------------------------
    long_avg_corr  = _book_avg_corr(returns_df, long_tickers)
    short_avg_corr = _book_avg_corr(returns_df, short_tickers)
    eff_n          = _effective_n_bets(all_tickers, returns_df)

    # -----------------------------------------------------------------------
    # Step 6 — alerts
    # -----------------------------------------------------------------------
    alerts: list[dict] = []

    if long_avg_corr > alert_avg_corr:
        alerts.append({
            "type":         "HIGH_BOOK_CORRELATION",
            "book":         "LONG",
            "avg_corr":     round(long_avg_corr, 4),
            "threshold":    alert_avg_corr,
            "priority":     "MEDIUM",
        })
        logger.warning(
            "correlation_monitor: LONG book avg_corr=%.4f > threshold=%.2f",
            long_avg_corr, alert_avg_corr,
        )

    if short_avg_corr > alert_avg_corr:
        alerts.append({
            "type":         "HIGH_BOOK_CORRELATION",
            "book":         "SHORT",
            "avg_corr":     round(short_avg_corr, 4),
            "threshold":    alert_avg_corr,
            "priority":     "MEDIUM",
        })
        logger.warning(
            "correlation_monitor: SHORT book avg_corr=%.4f > threshold=%.2f",
            short_avg_corr, alert_avg_corr,
        )

    # -----------------------------------------------------------------------
    # Step 7 — log and return
    # -----------------------------------------------------------------------
    result = {
        "long_avg_corr":    round(long_avg_corr,  4),
        "short_avg_corr":   round(short_avg_corr, 4),
        "effective_n_bets": round(eff_n,           2),
        "alerts":           alerts,
    }

    if not whatif:
        result_code = "WARNING" if alerts else "OK"
        reason = (
            f"long_avg_corr={long_avg_corr:.4f} short_avg_corr={short_avg_corr:.4f} "
            f"eff_n_bets={eff_n:.2f}"
        )
        _log_check(conn, score_date, "correlation_monitor", result_code, reason)

    logger.info(
        "correlation_monitor: %s | long_corr=%.4f short_corr=%.4f eff_n=%.2f alerts=%d",
        score_date, long_avg_corr, short_avg_corr, eff_n, len(alerts),
    )
    return result


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _book_tickers(positions_df: pd.DataFrame, direction: str) -> list[str]:
    """Return list of tickers in the named book."""
    if positions_df.empty or "direction" not in positions_df.columns:
        return []
    return positions_df[
        positions_df["direction"].str.upper() == direction
    ]["ticker"].tolist()


def _load_returns(
    conn: sa.engine.Connection,
    tickers: list[str],
    score_date: str,
    lookback_days: int,
) -> pd.DataFrame:
    """Load adj_close for tickers up to score_date, compute log returns.

    Tickers with fewer than _MIN_RETURNS return observations are dropped.
    Returns a DataFrame indexed by date with tickers as columns.
    """
    if not tickers:
        return pd.DataFrame()

    rows = conn.execute(
        sa.select(
            daily_prices.c.ticker,
            daily_prices.c.date,
            daily_prices.c.adj_close,
        ).where(
            daily_prices.c.ticker.in_(tickers) &
            (daily_prices.c.date <= score_date)
        ).order_by(daily_prices.c.date.asc())
    ).fetchall()

    if not rows:
        return pd.DataFrame()

    prices = (
        pd.DataFrame(rows, columns=["ticker", "date", "adj_close"])
        .pivot(index="date", columns="ticker", values="adj_close")
        .tail(lookback_days + 1)
    )

    log_returns = np.log(prices / prices.shift(1)).iloc[1:]

    # Drop tickers with insufficient history
    sufficient = log_returns.count() >= _MIN_RETURNS
    return log_returns.loc[:, sufficient]


def _book_avg_corr(returns_df: pd.DataFrame, tickers: list[str]) -> float:
    """Compute the mean of the upper-triangle of the pairwise correlation matrix.

    Returns 0.0 if fewer than 2 tickers are available in returns_df.
    """
    if returns_df.empty:
        return 0.0

    available = [t for t in tickers if t in returns_df.columns]
    if len(available) < 2:
        return 0.0

    book_returns = returns_df[available].dropna(how="all")
    if book_returns.shape[0] < 2:
        return 0.0

    corr_matrix = book_returns.corr()
    # Upper triangle, excluding diagonal
    n = len(available)
    upper_vals: list[float] = []
    for i in range(n):
        for j in range(i + 1, n):
            v = corr_matrix.iloc[i, j]
            if pd.notna(v):
                upper_vals.append(float(v))

    if not upper_vals:
        return 0.0

    return float(np.mean(upper_vals))


def _effective_n_bets(all_tickers: list[str], returns_df: pd.DataFrame) -> float:
    """Estimate the effective number of independent bets via eigenvalue entropy.

    Takes all available tickers from returns_df, computes their correlation
    matrix, and applies the exponential entropy of the normalised eigenvalue
    distribution.

    If fewer than 2 tickers are available returns float(len(tickers)).
    """
    if returns_df.empty:
        return float(len(all_tickers))

    available = [t for t in all_tickers if t in returns_df.columns]
    if len(available) < 2:
        return float(len(all_tickers))

    sub = returns_df[available].dropna(how="all")
    if sub.shape[0] < 2:
        return float(len(all_tickers))

    corr_matrix = sub.corr().values.astype(float)
    # Replace any NaN cells with 0 (off-diagonal) or 1 (diagonal)
    n = corr_matrix.shape[0]
    for i in range(n):
        for j in range(n):
            if np.isnan(corr_matrix[i, j]):
                corr_matrix[i, j] = 1.0 if i == j else 0.0

    eigenvalues = np.linalg.eigvalsh(corr_matrix)

    # Keep only positive eigenvalues
    pos_eigs = eigenvalues[eigenvalues > 0]
    if len(pos_eigs) == 0:
        return float(len(available))

    weights = pos_eigs / pos_eigs.sum()
    entropy = -float(np.sum(weights * np.log(weights + 1e-12)))
    return float(np.exp(entropy))


def _log_check(
    conn: sa.engine.Connection,
    run_date: str,
    check_type: str,
    result: str,
    reason: str,
) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
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
