"""Barra-style factor risk decomposition for the current portfolio."""
import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import sqlalchemy as sa

from data.db import daily_prices
from factors.db import factor_scores as factor_scores_table

logger = logging.getLogger(__name__)

_FACTOR_COLS = [
    "momentum_score", "quality_score", "value_score", "revisions_score",
    "insider_score", "growth_score", "short_interest_score", "institutional_score",
]
_ANNUALISE = 252.0
_MIN_RETURNS = 30
_MIN_REGRESSION_DAYS = 10
_SPECIFIC_VAR_FLOOR = 0.04        # (20% annualised vol)^2
_MCTR_FLAG_MULTIPLIER = 1.5


@dataclass
class FactorRiskResult:
    tickers: list[str] = field(default_factory=list)
    factor_cov: np.ndarray = field(default_factory=lambda: np.zeros((8, 8)))
    specific_var: dict[str, float] = field(default_factory=dict)
    factor_returns: pd.DataFrame = field(default_factory=pd.DataFrame)
    factor_contributions: dict[str, float] = field(default_factory=dict)
    total_vol: float = 0.0
    factor_var_pct: float = 0.0
    specific_var_pct: float = 0.0
    mctr: pd.Series = field(default_factory=pd.Series)
    mctr_flags: list[str] = field(default_factory=list)
    predicted_cov: np.ndarray = field(default_factory=lambda: np.zeros((0, 0)))


def compute_factor_risk(
    conn: sa.engine.Connection,
    positions_df: pd.DataFrame,
    score_date: str,
    lookback_days: int = 120,
) -> FactorRiskResult:
    """Compute Barra-style factor risk decomposition for the current portfolio.

    Parameters
    ----------
    conn:
        Active SQLAlchemy connection.
    positions_df:
        Portfolio positions with at minimum columns: ticker, weight.
        weight is signed (long +, short -).
    score_date:
        ISO date string used to look up factor scores.
    lookback_days:
        Number of trading days of price history to use for the regression.
    """
    if positions_df.empty:
        return FactorRiskResult()

    port_tickers = positions_df["ticker"].tolist()

    # --- Load universe factor scores ----------------------------------------
    rows = conn.execute(
        sa.select(
            factor_scores_table.c.ticker,
            *[factor_scores_table.c[col] for col in _FACTOR_COLS],
        ).where(factor_scores_table.c.score_date == score_date)
    ).fetchall()

    if not rows:
        logger.warning("factor_risk: no factor scores found for %s", score_date)
        return _fallback_result(port_tickers, positions_df)

    universe_scores = pd.DataFrame(rows, columns=["ticker"] + _FACTOR_COLS).set_index("ticker")
    universe_scores = universe_scores.dropna(how="all")
    universe_tickers = universe_scores.index.tolist()

    if not universe_tickers:
        return _fallback_result(port_tickers, positions_df)

    # --- Load prices and compute log returns ---------------------------------
    price_rows = conn.execute(
        sa.select(
            daily_prices.c.ticker,
            daily_prices.c.date,
            daily_prices.c.adj_close,
        ).where(
            daily_prices.c.ticker.in_(universe_tickers) &
            (daily_prices.c.date <= score_date)
        ).order_by(daily_prices.c.date.asc())
    ).fetchall()

    if not price_rows:
        logger.warning("factor_risk: no price data for universe on %s", score_date)
        return _fallback_result(port_tickers, positions_df)

    prices = (
        pd.DataFrame(price_rows, columns=["ticker", "date", "adj_close"])
        .pivot(index="date", columns="ticker", values="adj_close")
        .tail(lookback_days + 1)
    )
    log_returns = np.log(prices / prices.shift(1)).iloc[1:]

    # Drop tickers with insufficient history
    sufficient = log_returns.count() >= _MIN_RETURNS
    log_returns = log_returns.loc[:, sufficient]

    if log_returns.empty or log_returns.shape[0] < _MIN_REGRESSION_DAYS:
        logger.warning("factor_risk: insufficient return history")
        return _fallback_result(port_tickers, positions_df)

    return_tickers = log_returns.columns.tolist()

    # --- Align scores with return tickers ------------------------------------
    scores_aligned = universe_scores.reindex(return_tickers).dropna(how="all")
    common_tickers = scores_aligned.index.tolist()

    if not common_tickers:
        logger.warning("factor_risk: no overlap between scores and return tickers")
        return _fallback_result(port_tickers, positions_df)

    log_returns_aligned = log_returns[common_tickers]
    scores_filled = scores_aligned[_FACTOR_COLS].fillna(scores_aligned[_FACTOR_COLS].mean())

    # --- Standardise exposures X (z-score across universe) -------------------
    X_raw = scores_filled.values.astype(float)
    col_means = X_raw.mean(axis=0)
    col_stds = X_raw.std(axis=0, ddof=1)
    col_stds = np.where(col_stds == 0, 1.0, col_stds)
    X = (X_raw - col_means) / col_stds
    X_df = pd.DataFrame(X, index=common_tickers, columns=_FACTOR_COLS)

    # --- Cross-sectional regression: returns ~ factors (+ intercept) ---------
    returns_matrix = log_returns_aligned.values  # shape (T, N)
    T, N = returns_matrix.shape
    n_factors = len(_FACTOR_COLS)

    factor_returns_list: list[np.ndarray] = []
    specific_returns_cols: list[np.ndarray] = []
    successful_days = 0

    intercept_col = np.ones((N, 1))
    X_with_intercept = np.hstack([intercept_col, X])  # (N, 1+n_factors)

    for t in range(T):
        y = returns_matrix[t, :]
        if np.isnan(y).all():
            continue
        valid_mask = ~np.isnan(y)
        if valid_mask.sum() < n_factors + 2:
            continue
        y_valid = y[valid_mask]
        X_valid = X_with_intercept[valid_mask, :]
        try:
            coeffs, _, _, _ = np.linalg.lstsq(X_valid, y_valid, rcond=None)
        except np.linalg.LinAlgError:
            continue
        factor_ret = coeffs[1:]  # drop intercept
        fitted = X_with_intercept @ coeffs
        residuals = y - fitted
        residuals[~valid_mask] = np.nan
        factor_returns_list.append(factor_ret)
        specific_returns_cols.append(residuals)
        successful_days += 1

    # --- Fallback if insufficient regressions --------------------------------
    if successful_days < _MIN_REGRESSION_DAYS:
        logger.warning(
            "factor_risk: only %d regression days succeeded, using sample covariance fallback",
            successful_days,
        )
        return _sample_cov_fallback(port_tickers, positions_df, log_returns)

    factor_returns_arr = np.array(factor_returns_list)   # (successful_days, n_factors)
    specific_returns_arr = np.array(specific_returns_cols).T  # (N, successful_days)

    factor_returns_df = pd.DataFrame(
        factor_returns_arr, columns=_FACTOR_COLS
    )

    # --- Factor covariance (annualised) --------------------------------------
    if factor_returns_arr.shape[0] < 2:
        factor_cov = np.eye(n_factors) * _SPECIFIC_VAR_FLOOR
    else:
        factor_cov = np.cov(factor_returns_arr.T, ddof=1) * _ANNUALISE

    factor_cov = np.atleast_2d(factor_cov)

    # --- Specific variance per ticker (annualised) ---------------------------
    specific_var_dict: dict[str, float] = {}
    for i, ticker in enumerate(common_tickers):
        col = specific_returns_arr[i, :]
        col_clean = col[~np.isnan(col)]
        if len(col_clean) < 2:
            sv = _SPECIFIC_VAR_FLOOR
        else:
            sv = float(np.var(col_clean, ddof=1)) * _ANNUALISE
            sv = max(sv, _SPECIFIC_VAR_FLOOR)
        specific_var_dict[ticker] = sv

    # --- Portfolio weight vector ---------------------------------------------
    port_df = positions_df.set_index("ticker")
    port_in_universe = [t for t in port_tickers if t in X_df.index]

    if not port_in_universe:
        logger.warning("factor_risk: no portfolio tickers overlap with factor universe")
        return _fallback_result(port_tickers, positions_df)

    w = port_df.loc[port_in_universe, "weight"].values.astype(float)
    w = np.where(np.isnan(w), 0.0, w)

    # --- Exposure matrix for portfolio tickers -------------------------------
    X_port = X_df.loc[port_in_universe].values.astype(float)  # (N_port, n_factors)

    # --- Variance decomposition ----------------------------------------------
    portfolio_exposure = X_port.T @ w        # (n_factors,)
    F_times_exp = factor_cov @ portfolio_exposure  # (n_factors,)
    factor_var = float(portfolio_exposure @ F_times_exp)
    factor_var = max(factor_var, 0.0)

    spec_var_values = np.array([
        specific_var_dict.get(t, _SPECIFIC_VAR_FLOOR) for t in port_in_universe
    ])
    specific_var_port = float(np.dot(w ** 2, spec_var_values))
    specific_var_port = max(specific_var_port, 0.0)

    total_var = factor_var + specific_var_port

    if total_var <= 0:
        logger.warning("factor_risk: total_var <= 0, returning zero result")
        return FactorRiskResult(
            tickers=port_in_universe,
            factor_cov=factor_cov,
            specific_var=specific_var_dict,
            factor_returns=factor_returns_df,
        )

    total_vol = float(np.sqrt(total_var))

    # --- MCTR ----------------------------------------------------------------
    cov_matrix = X_port @ factor_cov @ X_port.T + np.diag(spec_var_values)
    cov_ri_rp = cov_matrix @ w
    mctr_values = w * cov_ri_rp / total_vol
    mctr_series = pd.Series(mctr_values, index=port_in_universe, name="mctr")

    mctr_flags: list[str] = []
    for ticker, mctr_i, w_i in zip(port_in_universe, mctr_values, w):
        abs_mctr_pct = abs(mctr_i / total_vol) if total_vol > 0 else 0.0
        abs_w = abs(w_i)
        if abs_mctr_pct > _MCTR_FLAG_MULTIPLIER * abs_w:
            mctr_flags.append(ticker)

    # --- Factor contributions ------------------------------------------------
    factor_contributions: dict[str, float] = {}
    for k, fname in enumerate(_FACTOR_COLS):
        contrib_k = float(portfolio_exposure[k] * F_times_exp[k])
        factor_contributions[fname] = contrib_k / total_var

    # --- Predicted covariance N×N --------------------------------------------
    predicted_cov = X_port @ factor_cov @ X_port.T + np.diag(spec_var_values)

    # Extend specific_var_dict to cover any portfolio tickers that were missed
    for ticker in port_tickers:
        if ticker not in specific_var_dict:
            specific_var_dict[ticker] = _SPECIFIC_VAR_FLOOR

    return FactorRiskResult(
        tickers=port_in_universe,
        factor_cov=factor_cov,
        specific_var=specific_var_dict,
        factor_returns=factor_returns_df,
        factor_contributions=factor_contributions,
        total_vol=total_vol,
        factor_var_pct=factor_var / total_var,
        specific_var_pct=specific_var_port / total_var,
        mctr=mctr_series,
        mctr_flags=mctr_flags,
        predicted_cov=predicted_cov,
    )


def save_predicted_cov(result: FactorRiskResult, cache_dir: Path, score_date: str) -> None:
    """Save predicted_cov as parquet (score_date-stamped and latest)."""
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        logger.warning("factor_risk: pyarrow not available — skipping predicted_cov save")
        return

    if result.predicted_cov.size == 0 or not result.tickers:
        logger.debug("factor_risk: empty predicted_cov, nothing to save")
        return

    cache_dir.mkdir(parents=True, exist_ok=True)
    cov_df = pd.DataFrame(
        result.predicted_cov,
        index=result.tickers,
        columns=result.tickers,
    )
    stamped_path = cache_dir / f"predicted_cov_{score_date}.parquet"
    latest_path = cache_dir / "predicted_cov_latest.parquet"
    cov_df.to_parquet(stamped_path)
    cov_df.to_parquet(latest_path)
    logger.debug("factor_risk: saved predicted_cov to %s and %s", stamped_path, latest_path)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fallback_result(port_tickers: list[str], positions_df: pd.DataFrame) -> FactorRiskResult:
    """Return a zero-variance result when factor data is unavailable."""
    specific_var = {t: _SPECIFIC_VAR_FLOOR for t in port_tickers}
    return FactorRiskResult(
        tickers=port_tickers,
        specific_var=specific_var,
        factor_var_pct=0.0,
        specific_var_pct=1.0,
    )


def _sample_cov_fallback(
    port_tickers: list[str],
    positions_df: pd.DataFrame,
    log_returns: pd.DataFrame,
) -> FactorRiskResult:
    """Factor regression failed — fall back to sample covariance of raw returns."""
    port_in_data = [t for t in port_tickers if t in log_returns.columns]
    if not port_in_data:
        return _fallback_result(port_tickers, positions_df)

    port_df = positions_df.set_index("ticker")
    w = port_df.reindex(port_in_data)["weight"].fillna(0.0).values.astype(float)

    ret_sub = log_returns[port_in_data].dropna()
    if ret_sub.shape[0] < 2:
        return _fallback_result(port_tickers, positions_df)

    sample_cov = ret_sub.cov().values * _ANNUALISE
    total_var = float(w @ sample_cov @ w)
    total_var = max(total_var, 0.0)
    total_vol = float(np.sqrt(total_var)) if total_var > 0 else 0.0

    specific_var_dict: dict[str, float] = {}
    for ticker in port_in_data:
        col = log_returns[ticker].dropna()
        sv = float(np.var(col.values, ddof=1)) * _ANNUALISE if len(col) >= 2 else _SPECIFIC_VAR_FLOOR
        specific_var_dict[ticker] = max(sv, _SPECIFIC_VAR_FLOOR)
    for ticker in port_tickers:
        if ticker not in specific_var_dict:
            specific_var_dict[ticker] = _SPECIFIC_VAR_FLOOR

    mctr_values = np.zeros(len(port_in_data))
    if total_vol > 0:
        cov_ri_rp = sample_cov @ w
        mctr_values = w * cov_ri_rp / total_vol
    mctr_series = pd.Series(mctr_values, index=port_in_data, name="mctr")

    mctr_flags: list[str] = []
    for ticker, mctr_i, w_i in zip(port_in_data, mctr_values, w):
        abs_mctr_pct = abs(mctr_i / total_vol) if total_vol > 0 else 0.0
        if abs_mctr_pct > _MCTR_FLAG_MULTIPLIER * abs(w_i):
            mctr_flags.append(ticker)

    return FactorRiskResult(
        tickers=port_in_data,
        factor_cov=np.zeros((8, 8)),
        specific_var=specific_var_dict,
        factor_returns=pd.DataFrame(),
        factor_contributions={f: 0.0 for f in _FACTOR_COLS},
        total_vol=total_vol,
        factor_var_pct=0.0,
        specific_var_pct=1.0,
        mctr=mctr_series,
        mctr_flags=mctr_flags,
        predicted_cov=sample_cov,
    )
