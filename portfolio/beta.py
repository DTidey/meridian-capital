"""Rolling beta calculation vs SPY."""

import logging

import numpy as np
import pandas as pd
import sqlalchemy as sa

from data.db import daily_prices

logger = logging.getLogger(__name__)

_BENCHMARK = "SPY"
_MIN_RETURNS = 20  # shared daily returns with SPY needed for an estimate
_BLUME_WEIGHT = 0.67


def compute_betas(
    conn: sa.engine.Connection,
    tickers: list[str],
    score_date: str,
    lookback_days: int = 60,
) -> pd.Series:
    """Return Series[ticker → Blume-adjusted beta vs SPY] from adj_close log-returns.

    Each ticker is regressed on SPY over the last lookback_days SPY returns, using only the
    days it shares with SPY, so other tickers' gaps never change its beta. Fewer than
    _MIN_RETURNS shared returns, or no SPY data, gives 1.0.
    """
    all_tickers = list(set(tickers) | {_BENCHMARK})
    rows = conn.execute(
        sa.select(daily_prices.c.ticker, daily_prices.c.date, daily_prices.c.adj_close)
        .where(daily_prices.c.ticker.in_(all_tickers) & (daily_prices.c.date <= score_date))
        .order_by(daily_prices.c.date.asc())
    ).fetchall()

    if not rows:
        logger.debug("Beta: no price data found")
        return pd.Series(dict.fromkeys(tickers, 1.0), name="beta")

    prices = pd.DataFrame(rows, columns=["ticker", "date", "adj_close"]).pivot(
        index="date", columns="ticker", values="adj_close"
    )
    # Day-on-day returns on SPY's trading days: a ticker's gap leaves NaN (dropped pairwise
    # below), and a peer's off-calendar row cannot break SPY's own returns
    if _BENCHMARK in prices.columns:
        prices = prices[prices[_BENCHMARK].notna()]
    returns = np.log(prices / prices.shift(1))
    spy_col = returns.get(_BENCHMARK, pd.Series(dtype=float))
    spy_ret = spy_col.dropna().tail(lookback_days)
    if len(spy_ret) < _MIN_RETURNS:
        logger.warning("Beta: insufficient SPY data, defaulting all to 1.0")
        return pd.Series(dict.fromkeys(tickers, 1.0), name="beta")

    betas = {t: _blume(_raw_beta(returns.get(t), spy_ret)) for t in tickers}
    return pd.Series(betas, name="beta")


def _raw_beta(ret: pd.Series | None, spy_ret: pd.Series) -> float:
    """OLS beta on the days shared with spy_ret; 1.0 with too few of them."""
    if ret is None:
        return 1.0
    shared = ret.reindex(spy_ret.index).dropna()
    if len(shared) < _MIN_RETURNS:
        return 1.0
    spy = spy_ret.loc[shared.index]
    var = spy.var()
    return float(shared.cov(spy) / var) if var > 0 else 1.0


def _blume(raw: float) -> float:
    """Blume adjustment: shrink towards the market beta of 1."""
    return _BLUME_WEIGHT * raw + (1.0 - _BLUME_WEIGHT)


def portfolio_beta(weights: pd.Series, betas: pd.Series) -> float:
    """Dot product of weights and betas — signed (long positive, short negative)."""
    aligned = weights.reindex(betas.index).fillna(0.0)
    return float((aligned * betas).sum())
