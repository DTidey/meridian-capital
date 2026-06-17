"""Rolling beta calculation vs SPY."""

import logging

import numpy as np
import pandas as pd
import sqlalchemy as sa

from data.db import daily_prices

logger = logging.getLogger(__name__)

_BENCHMARK = "SPY"


def compute_betas(
    conn: sa.engine.Connection,
    tickers: list[str],
    score_date: str,
    lookback_days: int = 60,
) -> pd.Series:
    """Return Series[ticker → beta vs SPY] using OLS on adj_close log-returns.

    Tickers with insufficient history default to beta = 1.0.
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

    prices = (
        pd.DataFrame(rows, columns=["ticker", "date", "adj_close"])
        .pivot(index="date", columns="ticker", values="adj_close")
        .tail(lookback_days + 1)
    )

    returns = np.log(prices / prices.shift(1)).dropna()

    if _BENCHMARK not in returns.columns or len(returns) < 10:
        logger.warning("Beta: insufficient SPY data, defaulting all to 1.0")
        return pd.Series(dict.fromkeys(tickers, 1.0), name="beta")

    spy_ret = returns[_BENCHMARK]
    spy_var = spy_ret.var()

    betas: dict[str, float] = {}
    for ticker in tickers:
        if ticker not in returns.columns or returns[ticker].dropna().shape[0] < 10:
            betas[ticker] = 1.0
        else:
            cov = returns[ticker].cov(spy_ret)
            betas[ticker] = cov / spy_var if spy_var > 0 else 1.0

    return pd.Series(betas, name="beta")


def portfolio_beta(weights: pd.Series, betas: pd.Series) -> float:
    """Dot product of weights and betas — signed (long positive, short negative)."""
    aligned = weights.reindex(betas.index).fillna(0.0)
    return float((aligned * betas).sum())
