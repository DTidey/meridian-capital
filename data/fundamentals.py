"""Fundamentals ingestion — quarterly + annual statements + 24 derived ratios."""

import logging
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import requests
import sqlalchemy as sa
import yfinance as yf

from .db import fundamentals, insert_or_replace
from .providers import FundamentalsProvider, Providers

logger = logging.getLogger(__name__)


def _safe(val: Any) -> float | None:
    try:
        f = float(val)
        return None if (f != f) else f
    except (TypeError, ValueError):
        return None


def _pct_change(current: float | None, prior: float | None) -> float | None:
    if current is None or prior is None or prior == 0:
        return None
    return (current - prior) / abs(prior)


def _get_val(df: pd.DataFrame | None, keys: list[str], col_idx: int = 0) -> float | None:
    """Extract a value from a yfinance statement DataFrame by trying multiple row labels."""
    if df is None or df.empty:
        return None
    cols = df.columns
    if col_idx >= len(cols):
        return None
    col = cols[col_idx]
    for key in keys:
        if key in df.index:
            return _safe(df.loc[key, col])
    return None


def _extract_statement_data(
    income: pd.DataFrame | None,
    balance: pd.DataFrame | None,
    cashflow: pd.DataFrame | None,
    col_idx: int,
) -> dict:
    """Pull raw line items from a single period (column index)."""

    def g(df, keys):
        return _get_val(df, keys, col_idx)

    revenue = g(income, ["Total Revenue", "Revenue"])
    gross = g(income, ["Gross Profit"])
    op_income = g(income, ["Operating Income", "EBIT"])
    ebit = g(income, ["EBIT", "Operating Income"])
    net_income = g(income, ["Net Income", "Net Income Common Stockholders"])
    rd = g(income, ["Research And Development", "Research Development"])

    total_assets = g(balance, ["Total Assets"])
    total_liab = g(balance, ["Total Liabilities Net Minority Interest", "Total Liabilities"])
    total_equity = g(
        balance, ["Total Stockholders Equity", "Stockholders Equity", "Common Stock Equity"]
    )
    cash = g(balance, ["Cash And Cash Equivalents", "Cash"])
    total_debt = g(balance, ["Total Debt", "Long Term Debt"])
    cur_assets = g(balance, ["Current Assets", "Total Current Assets"])
    cur_liab = g(balance, ["Current Liabilities", "Total Current Liabilities"])
    ar = g(balance, ["Accounts Receivable", "Net Receivables"])
    ret_earn = g(balance, ["Retained Earnings"])
    shares = g(balance, ["Share Issued", "Common Stock Shares Outstanding"])
    div_paid = g(cashflow, ["Cash Dividends Paid", "Common Stock Dividend Paid"])

    cfo = g(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"])
    capex = g(cashflow, ["Capital Expenditure", "Capital Expenditures"])
    buybacks = g(
        cashflow,
        ["Repurchase Of Capital Stock", "Repurchase Common Stock", "Common Stock Repurchase"],
    )

    fcf = None
    if cfo is not None and capex is not None:
        fcf = cfo - abs(capex)

    return {
        "revenue": revenue,
        "gross_profit": gross,
        "operating_income": op_income,
        "ebit": ebit,
        "net_income": net_income,
        "rd_expense": rd,
        "total_assets": total_assets,
        "total_liabilities": total_liab,
        "total_equity": total_equity,
        "cash": cash,
        "total_debt": total_debt,
        "current_assets": cur_assets,
        "current_liabilities": cur_liab,
        "accounts_receivable": ar,
        "retained_earnings": ret_earn,
        "shares_outstanding": shares,
        "dividends_paid": div_paid,
        "cfo": cfo,
        "capex": capex,
        "fcf": fcf,
        "buybacks": buybacks,
    }


def _compute_ratios(d: dict, prev: dict | None, prev_yoy: dict | None) -> dict:
    """Compute 24 derived ratios from current and prior period data."""
    rev = d["revenue"]
    gross = d["gross_profit"]
    op_in = d["operating_income"]
    ni = d["net_income"]
    eq = d["total_equity"]
    ast = d["total_assets"]
    dbt = d["total_debt"]
    cur_a = d["current_assets"]
    cur_l = d["current_liabilities"]
    ar = d["accounts_receivable"]
    cfo = d["cfo"]

    roe = _pct_change(ni, eq)
    roa = _safe(ni / ast) if ni and ast else None
    gross_margin = _safe(gross / rev) if gross and rev else None
    op_margin = _safe(op_in / rev) if op_in and rev else None
    net_margin = _safe(ni / rev) if ni and rev else None
    debt_equity = _safe(dbt / eq) if dbt and eq else None
    current_ratio = _safe(cur_a / cur_l) if cur_a and cur_l else None
    ar_to_rev = _safe(ar / rev) if ar and rev else None
    cfo_to_ni = _safe(cfo / ni) if cfo and ni else None
    working_cap = (cur_a - cur_l) if cur_a and cur_l else None
    asset_turnover = _safe(rev / ast) if rev and ast else None
    accruals = _safe((ni - cfo) / ast) if ni and cfo and ast else None

    rev_qoq = _pct_change(rev, prev["revenue"] if prev else None)
    ni_qoq = _pct_change(ni, prev["net_income"] if prev else None)
    rev_yoy = _pct_change(rev, prev_yoy["revenue"] if prev_yoy else None)
    ni_yoy = _pct_change(ni, prev_yoy["net_income"] if prev_yoy else None)

    return {
        "roe": roe,
        "roa": roa,
        "gross_margin": gross_margin,
        "operating_margin": op_margin,
        "net_margin": net_margin,
        "revenue_growth_yoy": rev_yoy,
        "revenue_growth_qoq": rev_qoq,
        "earnings_growth_yoy": ni_yoy,
        "earnings_growth_qoq": ni_qoq,
        "debt_to_equity": debt_equity,
        "current_ratio": current_ratio,
        "ar_to_revenue": ar_to_rev,
        "cfo_to_ni": cfo_to_ni,
        "accruals_ratio": accruals,
        "working_capital": working_cap,
        "asset_turnover": asset_turnover,
    }


def _upsert_period(
    conn: sa.engine.Connection,
    ticker: str,
    period_type: str,
    period_end: str,
    raw: dict,
    ratios: dict,
) -> None:
    conn.execute(
        insert_or_replace(conn, fundamentals).values(
            ticker=ticker,
            period_type=period_type,
            period_end=period_end,
            updated_at=datetime.utcnow().isoformat(timespec="seconds"),
            **raw,
            **ratios,
        )
    )


def _process_ticker_yfinance(conn: sa.engine.Connection, ticker: str) -> int:
    t = yf.Ticker(ticker)
    stored = 0

    for period_type, inc_df, bal_df, cf_df in [
        ("quarterly", t.quarterly_income_stmt, t.quarterly_balance_sheet, t.quarterly_cashflow),
        ("annual", t.income_stmt, t.balance_sheet, t.cashflow),
    ]:
        if inc_df is None or inc_df.empty:
            continue
        n_periods = inc_df.shape[1]
        period_data: list[dict] = []

        for i in range(n_periods):
            raw = _extract_statement_data(inc_df, bal_df, cf_df, i)
            period_data.append(raw)

        for i, raw in enumerate(period_data):
            prev = period_data[i + 1] if i + 1 < len(period_data) else None
            yoy_idx = i + 4 if period_type == "quarterly" else i + 1
            prev_yoy = period_data[yoy_idx] if yoy_idx < len(period_data) else None

            ratios = _compute_ratios(raw, prev, prev_yoy)
            col = inc_df.columns[i]
            period_end = (
                pd.Timestamp(col).strftime("%Y-%m-%d")
                if hasattr(col, "strftime")
                else str(col)[:10]
            )

            _upsert_period(conn, ticker, period_type, period_end, raw, ratios)
            stored += 1

    conn.commit()
    return stored


# ---------------------------------------------------------------------------
# FMP backend
# ---------------------------------------------------------------------------


def _fmp_get(endpoint: str, api_key: str, params: dict | None = None) -> list[dict]:
    # FMP retired /api/v3/ for new subscribers after Aug 2025; use /stable/ instead
    base = "https://financialmodelingprep.com/stable"
    p = {"apikey": api_key, **(params or {})}
    try:
        resp = requests.get(f"{base}/{endpoint}", params=p, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and "Error Message" in data:
            logger.warning("FMP error for %s: %s", endpoint, data["Error Message"])
            return []
        return data if isinstance(data, list) else []
    except Exception as exc:
        logger.debug("FMP request failed %s: %s", endpoint, exc)
        return []


def _fmp_raw(inc: dict, bal: dict, cf: dict) -> dict:
    """Map a single FMP period (three statement dicts) to our internal raw format."""
    cfo = _safe(cf.get("operatingCashFlow") or cf.get("netCashProvidedByOperatingActivities"))
    capex = _safe(cf.get("capitalExpenditure"))
    fcf = _safe(cf.get("freeCashFlow"))
    if fcf is None and cfo is not None and capex is not None:
        fcf = cfo - abs(capex)

    return {
        "revenue": _safe(inc.get("revenue")),
        "gross_profit": _safe(inc.get("grossProfit")),
        "operating_income": _safe(inc.get("operatingIncome")),
        "ebit": _safe(inc.get("operatingIncome")),
        "net_income": _safe(inc.get("netIncome")),
        "rd_expense": _safe(inc.get("researchAndDevelopmentExpenses")),
        "total_assets": _safe(bal.get("totalAssets")),
        "total_liabilities": _safe(bal.get("totalLiabilities")),
        "total_equity": _safe(bal.get("totalStockholdersEquity") or bal.get("totalEquity")),
        "cash": _safe(bal.get("cashAndCashEquivalents")),
        "total_debt": _safe(bal.get("totalDebt")),
        "current_assets": _safe(bal.get("totalCurrentAssets")),
        "current_liabilities": _safe(bal.get("totalCurrentLiabilities")),
        "accounts_receivable": _safe(bal.get("netReceivables")),
        "retained_earnings": _safe(bal.get("retainedEarnings")),
        "shares_outstanding": _safe(inc.get("weightedAverageShsOut")),
        "dividends_paid": _safe(cf.get("commonDividendsPaid") or cf.get("netDividendsPaid")),
        "cfo": cfo,
        "capex": capex,
        "fcf": fcf,
        "buybacks": _safe(cf.get("commonStockRepurchased")),
    }


def _process_ticker_fmp(conn: sa.engine.Connection, ticker: str, api_key: str) -> int:
    stored = 0

    for period_type, fmp_period, limit in [
        ("quarterly", "quarter", 16),
        ("annual", "annual", 5),
    ]:
        inc_rows = _fmp_get(
            "income-statement", api_key, {"symbol": ticker, "period": fmp_period, "limit": limit}
        )
        bal_rows = _fmp_get(
            "balance-sheet-statement",
            api_key,
            {"symbol": ticker, "period": fmp_period, "limit": limit},
        )
        cf_rows = _fmp_get(
            "cash-flow-statement", api_key, {"symbol": ticker, "period": fmp_period, "limit": limit}
        )

        if not inc_rows:
            continue

        bal_by_date = {r["date"]: r for r in bal_rows}
        cf_by_date = {r["date"]: r for r in cf_rows}

        period_data: list[tuple[str, dict]] = []
        for inc in inc_rows:
            date = inc.get("date", "")[:10]
            bal = bal_by_date.get(date, {})
            cf = cf_by_date.get(date, {})
            raw = _fmp_raw(inc, bal, cf)
            period_data.append((date, raw))

        for i, (period_end, raw) in enumerate(period_data):
            prev = period_data[i + 1][1] if i + 1 < len(period_data) else None
            yoy_idx = i + 4 if period_type == "quarterly" else i + 1
            prev_yoy = period_data[yoy_idx][1] if yoy_idx < len(period_data) else None

            ratios = _compute_ratios(raw, prev, prev_yoy)
            _upsert_period(conn, ticker, period_type, period_end, raw, ratios)
            stored += 1

    conn.commit()
    return stored


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _is_fresh(conn: sa.engine.Connection, ticker: str, max_age_days: int) -> bool:
    """Return True if this ticker's fundamentals were updated within max_age_days."""
    result = conn.execute(
        sa.select(sa.func.max(fundamentals.c.updated_at)).where(fundamentals.c.ticker == ticker)
    ).scalar()
    if result is None:
        return False
    try:
        return (datetime.utcnow() - datetime.fromisoformat(result)) < timedelta(days=max_age_days)
    except (ValueError, TypeError):
        return False


def update_fundamentals(
    conn: sa.engine.Connection,
    tickers: list[str],
    config: dict,
    providers: Providers,
) -> dict[str, int]:
    """Update fundamentals for all tickers. Returns {ticker: periods_stored}."""
    max_age_days = config.get("fundamentals", {}).get("refresh_days", 7)

    stale = [t for t in tickers if not _is_fresh(conn, t, max_age_days)]
    skipped = len(tickers) - len(stale)
    if skipped:
        logger.info("Skipping %d tickers with fundamentals < %d days old", skipped, max_age_days)

    summary: dict[str, int] = {t: 0 for t in tickers if t not in set(stale)}

    if not stale:
        logger.info("Fundamentals all fresh — nothing to fetch")
        return summary

    if providers.fundamentals == FundamentalsProvider.FMP:
        api_key = providers.fmp_key
        logger.info("Fetching fundamentals via FMP for %d tickers", len(stale))
        for ticker in stale:
            try:
                n = _process_ticker_fmp(conn, ticker, api_key)
                summary[ticker] = n
                if n:
                    logger.debug("FMP fundamentals %s: %d periods stored", ticker, n)
            except Exception as exc:
                logger.warning("FMP fundamentals failed for %s: %s", ticker, exc)
                summary[ticker] = 0
    else:
        logger.info("Fetching fundamentals via yfinance for %d tickers", len(stale))
        for ticker in stale:
            try:
                n = _process_ticker_yfinance(conn, ticker)
                summary[ticker] = n
                if n:
                    logger.debug("Fundamentals %s: %d periods stored", ticker, n)
            except Exception as exc:
                logger.warning("Fundamentals failed for %s: %s", ticker, exc)
                summary[ticker] = 0

    stored_total = sum(summary.values())
    updated = sum(1 for v in summary.values() if v > 0)
    logger.info(
        "Fundamentals complete — %d periods stored across %d tickers", stored_total, updated
    )
    return summary
