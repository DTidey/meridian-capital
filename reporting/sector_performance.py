"""90-day sector-relative alpha: portfolio picks vs sector ETF returns."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import sqlalchemy as sa

from data.db import daily_prices
from portfolio.db import portfolio_history

if TYPE_CHECKING:
    import sqlalchemy.engine

log = logging.getLogger(__name__)

_SECTOR_ETF_MAP = {
    "Information Technology": "XLK",
    "Financials": "XLF",
    "Health Care": "XLV",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Communication Services": "XLC",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
}


def compute(
    engine: sqlalchemy.engine.Engine,
    lookback_days: int = 90,
    sector_etf_map: dict | None = None,
) -> pd.DataFrame:
    """Compute sector-relative stock-selection alpha over lookback_days.

    Returns DataFrame(sector, portfolio_return, etf_return, alpha,
                       num_longs, num_shorts, winner).
    Also attaches attributes: total_alpha, winner_count, loser_count.
    """
    etf_map = sector_etf_map or _SECTOR_ETF_MAP
    cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()

    with engine.connect() as conn:
        hist = conn.execute(
            sa.select(
                portfolio_history.c.snapshot_date,
                portfolio_history.c.ticker,
                portfolio_history.c.direction,
                portfolio_history.c.weight,
                portfolio_history.c.sector,
            )
            .where(portfolio_history.c.snapshot_date >= cutoff)
            .order_by(portfolio_history.c.snapshot_date)
        ).fetchall()

        all_etfs = list(set(etf_map.values()))
        tickers = list({r[1] for r in hist}) + all_etfs
        price_rows = conn.execute(
            sa.select(daily_prices.c.date, daily_prices.c.ticker, daily_prices.c.adj_close)
            .where(daily_prices.c.ticker.in_(tickers))
            .where(daily_prices.c.date >= cutoff)
            .order_by(daily_prices.c.date)
        ).fetchall()

    if not hist:
        return pd.DataFrame(
            columns=[
                "sector",
                "portfolio_return",
                "etf_return",
                "alpha",
                "num_longs",
                "num_shorts",
                "winner",
            ]
        )

    hist_df = pd.DataFrame(hist, columns=["date", "ticker", "direction", "weight", "sector"])
    price_df = pd.DataFrame(price_rows, columns=["date", "ticker", "close"])
    price_pivot = price_df.pivot(index="date", columns="ticker", values="close")
    price_rets = price_pivot.pct_change()

    # Cumulative return from first date to last date
    dates = sorted(price_rets.index.tolist())
    if len(dates) < 2:
        return pd.DataFrame()

    _first_d, _last_d = dates[0], dates[-1]

    rows = []
    for sector, etf in etf_map.items():
        sector_hist = hist_df[hist_df["sector"] == sector]
        if sector_hist.empty:
            continue

        n_longs = sector_hist[sector_hist["direction"] == "LONG"]["ticker"].nunique()
        n_shorts = sector_hist[sector_hist["direction"] == "SHORT"]["ticker"].nunique()

        # Portfolio sector return: weighted average of position returns
        tickers_in_sector = sector_hist["ticker"].unique().tolist()
        ticker_rets = {}
        for t in tickers_in_sector:
            if t not in price_pivot.columns:
                continue
            p_start = price_pivot[t].dropna().iloc[0] if not price_pivot[t].dropna().empty else None
            p_end = price_pivot[t].dropna().iloc[-1] if not price_pivot[t].dropna().empty else None
            if p_start and p_end and p_start > 0:
                ticker_rets[t] = (p_end - p_start) / p_start

        if ticker_rets:
            port_sector_ret = float(np.mean(list(ticker_rets.values())))
        else:
            port_sector_ret = 0.0

        # ETF return over same period
        if etf in price_pivot.columns:
            etf_series = price_pivot[etf].dropna()
            if len(etf_series) >= 2:
                etf_ret = float((etf_series.iloc[-1] - etf_series.iloc[0]) / etf_series.iloc[0])
            else:
                etf_ret = 0.0
        else:
            etf_ret = 0.0

        alpha = port_sector_ret - etf_ret
        rows.append(
            {
                "sector": sector,
                "portfolio_return": round(port_sector_ret, 6),
                "etf_return": round(etf_ret, 6),
                "alpha": round(alpha, 6),
                "num_longs": n_longs,
                "num_shorts": n_shorts,
                "winner": alpha > 0,
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df.total_alpha = float(df["alpha"].sum())
    df.winner_count = int(df["winner"].sum())
    df.loser_count = int((~df["winner"]).sum())
    return df
