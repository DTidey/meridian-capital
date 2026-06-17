"""Quality factor — 8 sub-factors, sector percentile ranked."""

import logging

import numpy as np
import pandas as pd

from factors._utils import safe_div, sector_rank

logger = logging.getLogger(__name__)

COLS = [
    "qual_roe_stability",
    "qual_gm_level",
    "qual_gm_trend",
    "qual_de_inv",
    "qual_cfo_to_ni",
    "qual_accruals_inv",
    "qual_piotroski",
    "qual_altman_z",
]


def compute(data: dict[str, pd.DataFrame], config: dict) -> pd.DataFrame:
    """Return DataFrame indexed by ticker with quality sub-factor scores (0–100)."""
    universe = data["universe"]
    prices = data["prices"]
    funds = data["fundamentals"]
    min_size = config.get("scoring", {}).get("min_sector_size", 5)

    if universe.empty:
        return _empty_result(universe)

    sectors = universe.set_index("ticker")["sector"]

    latest_price = (
        (prices.sort_values("date").groupby("ticker")["close"].last())
        if not prices.empty
        else pd.Series(dtype=float)
    )

    tickers = universe["ticker"].tolist()
    raw = pd.DataFrame(index=tickers, columns=COLS, dtype=float)

    for ticker in tickers:
        ticker_funds = funds[funds["ticker"] == ticker].sort_values("period_end")
        if ticker_funds.empty:
            raw.loc[ticker] = np.nan
            continue

        latest = ticker_funds.iloc[-1]
        price = latest_price.get(ticker)

        raw.loc[ticker, "qual_roe_stability"] = _roe_stability(ticker_funds)
        raw.loc[ticker, "qual_gm_level"] = latest.get("gross_margin")
        raw.loc[ticker, "qual_gm_trend"] = _gm_trend(ticker_funds)
        raw.loc[ticker, "qual_de_inv"] = _de_inv(latest)
        raw.loc[ticker, "qual_cfo_to_ni"] = _cfo_to_ni(latest)
        raw.loc[ticker, "qual_accruals_inv"] = _accruals_inv(latest)
        raw.loc[ticker, "qual_piotroski"] = _piotroski(ticker_funds)
        raw.loc[ticker, "qual_altman_z"] = _altman_z(latest, price)

    # Cast to float (avoids object dtype issues from mixed None/float)
    raw = raw.astype(float)

    scored = pd.DataFrame(index=raw.index)
    for col in COLS:
        scored[col] = sector_rank(raw[col], sectors.reindex(raw.index), min_size)

    scored["quality_score"] = sector_rank(
        scored[COLS].mean(axis=1), sectors.reindex(scored.index), min_size
    )
    return scored


# ---------------------------------------------------------------------------
# Sub-factor calculations
# ---------------------------------------------------------------------------


def _roe_stability(df: pd.DataFrame) -> float:
    """Negative stdev of ROE over up to 12 quarters (higher = more stable)."""
    roes = df["roe"].dropna().tail(12)
    if len(roes) < 2:
        return np.nan
    return -float(roes.std())


def _gm_trend(df: pd.DataFrame) -> float:
    """Latest gross margin minus gross margin 4 quarters ago."""
    if len(df) < 5:
        return np.nan
    latest = df.iloc[-1]["gross_margin"]
    prior = df.iloc[-5]["gross_margin"] if len(df) >= 5 else None
    if latest is None or prior is None or np.isnan(float(latest)) or np.isnan(float(prior)):
        return np.nan
    return float(latest) - float(prior)


def _de_inv(row: pd.Series) -> float:
    """Inverted debt-to-equity (lower debt = higher score)."""
    de = row.get("debt_to_equity")
    if de is None or np.isnan(float(de)):
        return np.nan
    return -float(de)


def _cfo_to_ni(row: pd.Series) -> float:
    cfo = row.get("cfo")
    ni = row.get("net_income")
    if ni is None or ni == 0 or np.isnan(float(ni)):
        return np.nan
    if cfo is None or np.isnan(float(cfo)):
        return np.nan
    return float(cfo) / float(ni)


def _accruals_inv(row: pd.Series) -> float:
    """Inverted accruals ratio: -(NI - CFO) / TA."""
    ni = row.get("net_income")
    cfo = row.get("cfo")
    ta = row.get("total_assets")
    if any(v is None for v in [ni, cfo, ta]) or ta == 0:
        return np.nan
    return -(float(ni) - float(cfo)) / float(ta)


def _piotroski(df: pd.DataFrame) -> float:
    """Piotroski F-Score: sum of 9 binary signals (0–9)."""
    if len(df) < 2:
        return np.nan

    cur = df.iloc[-1]
    prev = df.iloc[-2]

    def _flag(condition) -> int:
        try:
            return 1 if condition else 0
        except (TypeError, ValueError):
            return 0

    roa_cur = safe_div(cur.get("net_income"), cur.get("total_assets"))
    roa_prev = safe_div(prev.get("net_income"), prev.get("total_assets"))
    cfo_cur = cur.get("cfo") or 0.0
    ni_cur = cur.get("net_income") or 0.0

    de_cur = cur.get("debt_to_equity") or 0.0
    de_prev = prev.get("debt_to_equity") or 0.0
    cr_cur = cur.get("current_ratio") or 0.0
    cr_prev = prev.get("current_ratio") or 0.0
    sh_cur = cur.get("shares_outstanding") or 0.0
    sh_prev = prev.get("shares_outstanding") or 0.0
    gm_cur = cur.get("gross_margin") or 0.0
    gm_prev = prev.get("gross_margin") or 0.0
    at_cur = cur.get("asset_turnover") or 0.0
    at_prev = prev.get("asset_turnover") or 0.0

    signals = [
        _flag(roa_cur is not None and roa_cur > 0),  # 1. ROA > 0
        _flag(cfo_cur > 0),  # 2. CFO > 0
        _flag(roa_cur is not None and roa_prev is not None and roa_cur > roa_prev),  # 3. Rising ROA
        _flag(cfo_cur > ni_cur),  # 4. CFO > NI
        _flag(de_cur <= de_prev),  # 5. Falling D/E
        _flag(cr_cur >= cr_prev),  # 6. Rising current ratio
        _flag(sh_cur <= sh_prev),  # 7. No dilution
        _flag(gm_cur >= gm_prev),  # 8. Rising gross margin
        _flag(at_cur >= at_prev),  # 9. Rising asset turnover
    ]
    return float(sum(signals))


def _altman_z(row: pd.Series, price) -> float:
    """Altman Z-Score for public manufacturing firms."""
    wc = row.get("working_capital")
    ta = row.get("total_assets")
    re = row.get("retained_earnings")
    ebit = row.get("ebit")
    tl = row.get("total_liabilities")
    sales = row.get("revenue")
    shares = row.get("shares_outstanding")

    if any(v is None for v in [wc, ta, re, ebit, tl, sales, shares, price]):
        return np.nan
    if ta == 0 or tl == 0:
        return np.nan

    ta, re, ebit, tl, sales, wc, shares = (
        float(ta),
        float(re),
        float(ebit),
        float(tl),
        float(sales),
        float(wc),
        float(shares),
    )
    mkt_cap = shares * float(price)

    z = (
        1.2 * (wc / ta)
        + 1.4 * (re / ta)
        + 3.3 * (ebit / ta)
        + 0.6 * (mkt_cap / tl)
        + 1.0 * (sales / ta)
    )
    return z


def _empty_result(universe: pd.DataFrame) -> pd.DataFrame:
    tickers = universe["ticker"].tolist() if not universe.empty else []
    return pd.DataFrame(50.0, index=tickers, columns=COLS + ["quality_score"])
