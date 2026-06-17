"""MVO optimizer (Markowitz SLSQP). Falls back to conviction-tilt on failure."""

import logging

import numpy as np
import pandas as pd
import sqlalchemy as sa
from scipy.optimize import minimize

from data.db import daily_prices
from portfolio import optimizer as tilt
from portfolio.transaction_costs import estimate_cost

logger = logging.getLogger(__name__)

_MIN_HISTORY = 60


def optimise(
    candidates: pd.DataFrame,
    prices: dict[str, pd.DataFrame],
    betas: pd.Series,
    config: dict,
    score_date: str,
    conn: sa.engine.Connection,
) -> pd.DataFrame:
    """MVO-optimised target portfolio, falling back to conviction-tilt on failure."""
    try:
        return _mvo(candidates, prices, betas, config, score_date, conn)
    except Exception as exc:
        logger.warning("MVO did not converge (%s) — falling back to conviction-tilt", exc)
        return tilt.optimise(candidates, prices, betas, config, score_date, conn)


def _mvo(
    candidates: pd.DataFrame,
    prices: dict[str, pd.DataFrame],
    betas: pd.Series,
    config: dict,
    score_date: str,
    conn: sa.engine.Connection,
) -> pd.DataFrame:
    pcfg = config.get("portfolio", {})
    mvo_cfg = pcfg.get("mvo", {})
    _tc_cfg = pcfg.get("transaction_costs", {})

    nav = float(pcfg.get("nav_usd", 10_000_000))
    long_gross = float(pcfg.get("target_long_gross", 0.90))
    short_gross = float(pcfg.get("target_short_gross", 0.60))
    max_pos = float(pcfg.get("max_position_pct", 0.05))
    min_pos = float(pcfg.get("min_position_pct", 0.005))
    max_sector = float(pcfg.get("max_sector_pct", 0.25))
    max_sector_net = float(pcfg.get("max_sector_net_pct", 0.05))
    max_beta = float(pcfg.get("max_beta", 0.15))
    lam = float(mvo_cfg.get("risk_aversion", 1.0))
    cov_days = int(mvo_cfg.get("cov_lookback_days", 120))
    max_iter = int(mvo_cfg.get("max_iter", 1000))
    ret_map = mvo_cfg.get("score_to_return_map", {})
    ret_100 = float(ret_map.get("score_100", 0.15))
    ret_0 = float(ret_map.get("score_0", -0.15))
    num_longs = int(pcfg.get("num_longs", 20))
    num_shorts = int(pcfg.get("num_shorts", 20))

    longs = (
        candidates[candidates["direction"] == "LONG"]
        .sort_values("combined_score", ascending=False)
        .head(num_longs)
        .copy()
    )
    shorts = (
        candidates[candidates["direction"] == "SHORT"]
        .sort_values("combined_score", ascending=True)
        .head(num_shorts)
        .copy()
    )

    if longs.empty or shorts.empty:
        raise ValueError("Insufficient candidates for MVO")

    all_cands = pd.concat([longs, shorts], ignore_index=True)
    tickers = all_cands["ticker"].tolist()
    _n = len(tickers)
    n_long = len(longs)
    n_short = len(shorts)

    # Expected returns (score → linear interpolation)
    scores = all_cands["combined_score"].values
    mu_gross = ret_0 + (scores / 100.0) * (ret_100 - ret_0)

    # Adjust for transaction costs (use conviction weights as proxy for position size)
    tilt_result = tilt.optimise(candidates, prices, betas, config, score_date, conn)
    mu = _adjust_mu_for_costs(mu_gross, tickers, tilt_result, prices, nav, config)

    # Covariance matrix
    sigma = _build_cov(tickers, score_date, cov_days, conn)

    # Warm start: conviction-tilt weights
    w0 = _warm_start(tickers, n_long, n_short, long_gross, short_gross, tilt_result)

    # Sector membership
    sector_of = dict(zip(all_cands["ticker"], all_cands["sector"].fillna("Unknown"), strict=False))

    constraints, bounds = _build_constraints(
        tickers,
        n_long,
        n_short,
        long_gross,
        short_gross,
        min_pos,
        max_pos,
        max_sector,
        max_sector_net,
        max_beta,
        betas,
        sector_of,
    )

    def objective(w):
        return -(mu @ w) + lam * (w @ sigma @ w)

    result = minimize(
        objective,
        w0,
        method="SLSQP",
        bounds=bounds,
        constraints=constraints,
        options={"maxiter": max_iter, "ftol": 1e-9},
    )

    if not result.success:
        raise RuntimeError(f"SLSQP: {result.message}")

    w_opt = result.x
    return _weights_to_df(all_cands, w_opt, n_long, prices, betas, nav)


def _adjust_mu_for_costs(
    mu_gross: np.ndarray,
    tickers: list[str],
    tilt_result: pd.DataFrame,
    prices: dict[str, pd.DataFrame],
    nav: float,
    config: dict,
) -> np.ndarray:
    mu = mu_gross.copy()
    tilt_map = (
        tilt_result.set_index("ticker")["weight"].abs().to_dict() if not tilt_result.empty else {}
    )
    for i, ticker in enumerate(tickers):
        price_df = prices.get(ticker, pd.DataFrame())
        if price_df.empty:
            continue
        close_col = "close" if "close" in price_df.columns else "adj_close"
        if close_col not in price_df.columns:
            continue
        price = float(price_df[close_col].iloc[-1])
        weight = tilt_map.get(ticker, 0.01)
        pos_value = weight * nav
        trade_shares = pos_value / price if price > 0 else 0.0
        cost = estimate_cost(ticker, trade_shares, price, price_df, config)
        if pos_value > 0:
            mu[i] -= cost / pos_value
    return mu


def _build_cov(
    tickers: list[str],
    score_date: str,
    cov_days: int,
    conn: sa.engine.Connection,
) -> np.ndarray:
    rows = conn.execute(
        sa.select(daily_prices.c.ticker, daily_prices.c.date, daily_prices.c.adj_close)
        .where(daily_prices.c.ticker.in_(tickers) & (daily_prices.c.date <= score_date))
        .order_by(daily_prices.c.date.asc())
    ).fetchall()

    if not rows:
        raise ValueError("No price data for covariance computation")

    prices_df = (
        pd.DataFrame(rows, columns=["ticker", "date", "adj_close"])
        .pivot(index="date", columns="ticker", values="adj_close")
        .tail(cov_days + 1)
    )
    returns = np.log(prices_df / prices_df.shift(1)).dropna()

    if len(returns) < _MIN_HISTORY:
        raise ValueError(f"Insufficient history for covariance: {len(returns)} < {_MIN_HISTORY}")

    # Reorder columns to match tickers list; fill missing with market proxy
    returns = returns.reindex(columns=tickers)
    # Fill any missing tickers with the cross-sectional mean return
    cross_mean = returns.mean(axis=1)
    for col in returns.columns:
        if returns[col].isna().all():
            returns[col] = cross_mean
    returns = returns.fillna(returns.mean())

    sigma = returns.cov().values * 252  # annualise
    # Regularise: add small ridge to ensure positive-definite
    sigma += np.eye(len(tickers)) * 1e-6
    return sigma


def _warm_start(
    tickers: list[str],
    n_long: int,
    n_short: int,
    long_gross: float,
    short_gross: float,
    tilt_result: pd.DataFrame,
) -> np.ndarray:
    w0 = np.zeros(len(tickers))
    if not tilt_result.empty:
        tilt_map = tilt_result.set_index("ticker")["weight"].to_dict()
        for i, t in enumerate(tickers):
            w0[i] = tilt_map.get(t, 0.0)
    else:
        w0[:n_long] = long_gross / max(n_long, 1)
        w0[n_long:] = -short_gross / max(n_short, 1)
    return w0


def _build_constraints(
    tickers,
    n_long,
    n_short,
    long_gross,
    short_gross,
    min_pos,
    max_pos,
    max_sector,
    max_sector_net,
    max_beta,
    betas,
    sector_of,
):
    long_idx = list(range(n_long))
    short_idx = list(range(n_long, n_long + n_short))

    constraints = [
        # Long weights sum to long_gross
        {"type": "eq", "fun": lambda w: sum(w[i] for i in long_idx) - long_gross},
        # Short weights sum (in absolute) to short_gross
        {"type": "eq", "fun": lambda w: -sum(w[i] for i in short_idx) - short_gross},
        # Net beta ≤ max_beta
        {
            "type": "ineq",
            "fun": lambda w: (
                max_beta - abs(sum(w[i] * betas.get(tickers[i], 1.0) for i in range(len(tickers))))
            ),
        },
    ]

    # Sector constraints
    sectors = list(set(sector_of.values()))
    for sector in sectors:
        s_long = [i for i in long_idx if sector_of.get(tickers[i]) == sector]
        s_short = [i for i in short_idx if sector_of.get(tickers[i]) == sector]
        if s_long:
            constraints.append(
                {"type": "ineq", "fun": lambda w, sl=s_long: max_sector - sum(w[i] for i in sl)}
            )
        if s_short:
            constraints.append(
                {"type": "ineq", "fun": lambda w, ss=s_short: max_sector + sum(w[i] for i in ss)}
            )
        if s_long and s_short:
            constraints.append(
                {
                    "type": "ineq",
                    "fun": lambda w, sl=s_long, ss=s_short: (
                        max_sector_net - abs(sum(w[i] for i in sl) + sum(w[i] for i in ss))
                    ),
                }
            )

    bounds = [(min_pos, max_pos)] * n_long + [(-max_pos, -min_pos)] * n_short
    return constraints, bounds


def _weights_to_df(
    all_cands: pd.DataFrame,
    weights: np.ndarray,
    n_long: int,
    prices: dict[str, pd.DataFrame],
    betas: pd.Series,
    nav: float,
) -> pd.DataFrame:
    df = all_cands[["ticker", "direction", "sector", "combined_score"]].copy()
    df["weight"] = weights
    df["beta"] = df["ticker"].map(betas).fillna(1.0)

    prices_list = []
    shares_list = []
    for _, row in df.iterrows():
        price_df = prices.get(row["ticker"], pd.DataFrame())
        close_col = "close" if (not price_df.empty and "close" in price_df.columns) else "adj_close"
        price = (
            float(price_df[close_col].iloc[-1])
            if (not price_df.empty and close_col in price_df.columns)
            else 0.0
        )
        pos_value = abs(row["weight"]) * nav
        shares = pos_value / price if price > 0 else 0.0
        if row["direction"] == "SHORT":
            shares = -shares
        prices_list.append(price)
        shares_list.append(shares)

    df["current_price"] = prices_list
    df["shares"] = shares_list
    return df[
        [
            "ticker",
            "direction",
            "weight",
            "shares",
            "sector",
            "combined_score",
            "beta",
            "current_price",
        ]
    ].reset_index(drop=True)
