"""Value factor — 6 sub-factors, sector percentile ranked."""

import logging

import numpy as np
import pandas as pd

from factors._utils import guarded_current, latest_trends, safe_div, sector_rank
from factors.market_cap import market_caps
from factors.ttm import num, ttm

logger = logging.getLogger(__name__)

COLS = [
    "val_fwd_earn_yield",
    "val_book_to_price",
    "val_fcf_yield",
    "val_ev_ebitda_inv",
    "val_shareholder_yield",
    "val_sales_to_ev",
]


def compute(data: dict[str, pd.DataFrame], config: dict) -> pd.DataFrame:
    """Return DataFrame indexed by ticker with value sub-factor scores (0–100)."""
    universe = data["universe"]
    prices = data["prices"]
    funds = data["fundamentals"]
    trends = latest_trends(data["estimate_trends"])
    min_size = config.get("scoring", {}).get("min_sector_size", 5)

    if universe.empty:
        return _empty_result(universe)

    sectors = universe.set_index("ticker")["sector"]
    latest_price = prices.sort_values("date").groupby("ticker")["close"].last()
    mcaps = market_caps(prices, data.get("market_caps"), funds)
    quarters = _quarters_newest_first(funds)

    tickers = universe["ticker"].tolist()
    raw = pd.DataFrame(np.nan, index=tickers, columns=COLS, dtype=float)
    for ticker in tickers:
        price = latest_price.get(ticker)
        if not price or ticker not in quarters:
            continue
        # Guarded +1y consensus from the latest fresh earningsTrend snapshot
        eps = guarded_current(trends.loc[ticker]) if ticker in trends.index else None
        raw.loc[ticker, "val_fwd_earn_yield"] = safe_div(eps, price)
        raw.loc[ticker, COLS[1:]] = _statement_yields(quarters[ticker], mcaps.get(ticker))

    # Rank within sector
    scored = pd.DataFrame(index=raw.index)
    for col in COLS:
        scored[col] = sector_rank(raw[col], sectors.reindex(raw.index), min_size)

    scored["value_score"] = sector_rank(
        scored[COLS].mean(axis=1), sectors.reindex(scored.index), min_size
    )
    return scored


def _quarters_newest_first(funds: pd.DataFrame) -> dict[str, list[dict]]:
    if funds.empty:
        return {}
    ordered = funds.sort_values("period_end", ascending=False).copy()
    ordered["period_end"] = pd.to_datetime(ordered["period_end"]).dt.strftime("%Y-%m-%d")
    return {t: g.to_dict("records") for t, g in ordered.groupby("ticker")}


def _statement_yields(quarters: list[dict], mcap: float | None) -> list[float]:
    """Book/price, FCF yield, EBIT/EV, shareholder yield and sales/EV (COLS[1:] order).

    Flows are trailing twelve months; balance-sheet items all come from the latest quarter
    that has a balance sheet, so no row mixes periods.
    """
    nan = np.nan
    if not mcap or np.isnan(mcap) or mcap <= 0:
        return [nan] * 5
    bs = next((q for q in quarters if num(q.get("total_assets")) is not None), None)
    equity = num(bs.get("total_equity")) if bs else None
    debt = (num(bs.get("total_debt")) or 0.0) if bs else None  # omitted line: no debt
    cash = num(bs.get("cash")) if bs else None
    ev = mcap + debt - cash if debt is not None and cash is not None else None
    ebit, rev, fcf = (ttm(quarters, f) for f in ("operating_income", "revenue", "fcf"))
    divs, bbacks = ttm(quarters, "dividends_paid"), ttm(quarters, "buybacks")
    returned = None if divs is None and bbacks is None else (divs or 0.0) + (bbacks or 0.0)
    ev_ok = ev is not None and ev > 0
    return [
        equity / mcap if equity is not None else nan,
        fcf / mcap if fcf is not None else nan,
        ebit / ev if ev_ok and ebit is not None and ebit > 0 else nan,
        returned / mcap if returned is not None else nan,
        rev / ev if ev_ok and rev else nan,
    ]


def _empty_result(universe: pd.DataFrame) -> pd.DataFrame:
    tickers = universe["ticker"].tolist() if not universe.empty else []
    return pd.DataFrame(50.0, index=tickers, columns=COLS + ["value_score"])
