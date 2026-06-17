"""Value factor — 6 sub-factors, sector percentile ranked."""

import logging

import numpy as np
import pandas as pd

from factors._utils import safe_div, sector_rank

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
    estimates = data["estimates"]
    min_size = config.get("scoring", {}).get("min_sector_size", 5)

    if universe.empty:
        return _empty_result(universe)

    sectors = universe.set_index("ticker")["sector"]

    # Latest close per ticker
    latest_price = prices.sort_values("date").groupby("ticker")["close"].last()

    # Latest quarterly fundamentals per ticker
    latest_fund = funds.sort_values("period_end").groupby("ticker").last()

    # Latest estimate per ticker
    latest_est = (
        (estimates.sort_values("date").groupby("ticker")["eps_estimate_fwd"].last())
        if not estimates.empty
        else pd.Series(dtype=float)
    )

    tickers = universe["ticker"].tolist()
    raw = pd.DataFrame(index=tickers)

    for ticker in tickers:
        price = latest_price.get(ticker)
        fund = latest_fund.loc[ticker] if ticker in latest_fund.index else None
        eps = latest_est.get(ticker) if not latest_est.empty else None

        if price is None or price == 0 or fund is None:
            for col in COLS:
                raw.loc[ticker, col] = np.nan
            continue

        shares = fund.get("shares_outstanding") if fund is not None else None
        equity = fund.get("total_equity")
        fcf = fund.get("fcf")
        debt = fund.get("total_debt") or 0.0
        cash = fund.get("cash") or 0.0
        ebit = fund.get("ebit")
        rev = fund.get("revenue")
        divs = abs(fund.get("dividends_paid") or 0.0)
        bbacks = abs(fund.get("buybacks") or 0.0)

        mkt_cap = (shares or 0) * price
        ev = mkt_cap + (debt or 0) - (cash or 0) if mkt_cap > 0 else None

        raw.loc[ticker, "val_fwd_earn_yield"] = safe_div(eps, price)

        raw.loc[ticker, "val_book_to_price"] = (
            safe_div(safe_div(equity, shares), price)
            if equity is not None and shares and shares > 0
            else np.nan
        )

        raw.loc[ticker, "val_fcf_yield"] = safe_div(fcf, mkt_cap) if mkt_cap > 0 else np.nan

        if ev and ev > 0 and ebit and ebit > 0:
            raw.loc[ticker, "val_ev_ebitda_inv"] = safe_div(ebit, ev)
        else:
            raw.loc[ticker, "val_ev_ebitda_inv"] = np.nan

        raw.loc[ticker, "val_shareholder_yield"] = (
            (divs + bbacks) / mkt_cap if mkt_cap > 0 else np.nan
        )

        if ev and ev > 0 and rev:
            raw.loc[ticker, "val_sales_to_ev"] = safe_div(rev, ev)
        else:
            raw.loc[ticker, "val_sales_to_ev"] = np.nan

    # Rank within sector
    scored = pd.DataFrame(index=raw.index)
    for col in COLS:
        scored[col] = sector_rank(raw[col], sectors.reindex(raw.index), min_size)

    scored["value_score"] = sector_rank(
        scored[COLS].mean(axis=1), sectors.reindex(scored.index), min_size
    )
    return scored


def _empty_result(universe: pd.DataFrame) -> pd.DataFrame:
    tickers = universe["ticker"].tolist() if not universe.empty else []
    return pd.DataFrame(50.0, index=tickers, columns=COLS + ["value_score"])
