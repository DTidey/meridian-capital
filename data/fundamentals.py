"""Fundamentals ingestion — quarterly + annual statements + 24 derived ratios."""

import logging
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import requests
import sqlalchemy as sa
import yfinance as yf

from data._utils import utc_now

from .db import _dialect_insert, fundamentals
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
    shares = _shares_outstanding(balance, col_idx)
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


def _shares_outstanding(balance: pd.DataFrame | None, col_idx: int) -> float | None:
    """Outstanding shares; "Share Issued" alone would include treasury stock."""
    ordinary = _get_val(balance, ["Ordinary Shares Number"], col_idx)
    if ordinary is not None:
        return ordinary
    issued = _get_val(balance, ["Share Issued"], col_idx)
    treasury = _get_val(balance, ["Treasury Shares Number"], col_idx)
    if issued is not None and treasury is not None:
        return issued - treasury
    if issued is not None:
        return issued
    return _get_val(balance, ["Common Stock Shares Outstanding"], col_idx)


def month_end(date_str: str) -> str:
    """Nearest calendar month-end: fiscal 52/53-week ends land within days of one."""
    d = pd.Timestamp(str(date_str)[:10])
    this_end = d + pd.offsets.MonthEnd(0)
    prev_end = d - pd.offsets.MonthEnd(1) if d != this_end else this_end
    nearest = prev_end if (d - prev_end) < (this_end - d) else this_end
    return nearest.strftime("%Y-%m-%d")


def _align(df: pd.DataFrame | None, columns) -> pd.DataFrame | None:
    """Reorder a statement's columns to match the income statement's periods (by month-end).

    Yahoo sometimes omits a period from one statement, so matching by position would pair
    one quarter's income with another quarter's balance sheet.
    """
    if df is None or df.empty:
        return df
    # Newest first from Yahoo; reversed so the newest column wins a shared month-end
    by_month = {month_end(str(c)): c for c in reversed(df.columns)}
    picked = [by_month.get(month_end(str(c))) for c in columns]
    aligned = pd.DataFrame(
        {
            i: (df[c] if c is not None else pd.Series(float("nan"), index=df.index))
            for i, c in enumerate(picked)
        }
    )
    aligned.columns = list(columns)
    return aligned


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


def _prior_period(period_data: list[dict], ends: list[str], i: int, months: int) -> dict | None:
    """The fetched period exactly `months` before period i (by month-end), or None."""
    target = month_end((pd.Timestamp(ends[i]) - pd.DateOffset(months=months)).strftime("%Y-%m-%d"))
    return period_data[ends.index(target)] if target in ends else None


def _upsert_period(
    conn: sa.engine.Connection,
    ticker: str,
    period_type: str,
    period_end: str,
    raw: dict,
    ratios: dict,
) -> None:
    """Insert or update a period, keeping stored values where the new fetch has none.

    Yahoo keeps listing aging quarters after blanking their figures; overwriting with those
    blanks would erase the history that multi-quarter measures rely on. Raw figures are merged
    field by field. Ratios are kept only when the fetch has no figures at all; otherwise they
    come from this fetch, so a ratio never outlives the figures it was computed from.
    """
    raw_vals = {k: _safe(v) for k, v in raw.items()}
    ratio_vals = {k: _safe(v) for k, v in ratios.items()}
    stmt = _dialect_insert(conn)(fundamentals).values(
        ticker=ticker,
        period_type=period_type,
        period_end=month_end(period_end),
        updated_at=utc_now().isoformat(timespec="seconds"),
        **raw_vals,
        **ratio_vals,
    )
    blank_fetch = all(v is None for v in raw_vals.values())
    merged = {c: sa.func.coalesce(stmt.excluded[c], fundamentals.c[c]) for c in raw_vals}
    merged |= {
        c: (
            sa.func.coalesce(stmt.excluded[c], fundamentals.c[c])
            if blank_fetch
            else stmt.excluded[c]
        )
        for c in ratio_vals
    }
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=["ticker", "period_type", "period_end"],
            set_={**merged, "updated_at": stmt.excluded.updated_at},
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
        bal_df, cf_df = _align(bal_df, inc_df.columns), _align(cf_df, inc_df.columns)
        n_periods = inc_df.shape[1]
        period_data: list[dict] = []

        for i in range(n_periods):
            raw = _extract_statement_data(inc_df, bal_df, cf_df, i)
            period_data.append(raw)

        ends = [month_end(str(c)) for c in inc_df.columns]
        step = 3 if period_type == "quarterly" else 12
        for i, raw in enumerate(period_data):
            prev = _prior_period(period_data, ends, i, step)
            prev_yoy = _prior_period(period_data, ends, i, 12)

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

        ends = [month_end(d) for d, _ in period_data]
        raws = [r for _, r in period_data]
        step = 3 if period_type == "quarterly" else 12
        for i, (period_end, raw) in enumerate(period_data):
            prev = _prior_period(raws, ends, i, step)
            prev_yoy = _prior_period(raws, ends, i, 12)

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
        return (utc_now() - datetime.fromisoformat(result)) < timedelta(days=max_age_days)
    except (ValueError, TypeError):
        return False


def merge_period_duplicates(conn: sa.engine.Connection) -> int:
    """Keep one row per ticker, type and month-end period (the newest); return rows deleted."""
    rows = conn.execute(
        sa.select(
            fundamentals.c.ticker,
            fundamentals.c.period_type,
            fundamentals.c.period_end,
            fundamentals.c.updated_at,
        )
    ).fetchall()
    groups: dict[tuple[str, str, str], list] = {}
    for r in rows:
        groups.setdefault((r.ticker, r.period_type, month_end(r.period_end)), []).append(r)

    deleted = 0
    for (ticker, ptype, end), members in groups.items():
        # Newest fetch wins; on a tie prefer the row already on the month-end
        members.sort(key=lambda r: (r.updated_at or "", r.period_end == end), reverse=True)
        keep, drop = members[0], members[1:]
        for r in drop:
            conn.execute(sa.delete(fundamentals).where(_row_key(ticker, ptype, r.period_end)))
            deleted += 1
        if keep.period_end != end:
            conn.execute(
                sa.update(fundamentals)
                .where(_row_key(ticker, ptype, keep.period_end))
                .values(period_end=end)
            )
    conn.commit()
    if deleted:
        logger.warning("Merged %d duplicate fundamentals rows (same month-end period)", deleted)
    return deleted


def _row_key(ticker: str, period_type: str, period_end: str):
    return (
        (fundamentals.c.ticker == ticker)
        & (fundamentals.c.period_type == period_type)
        & (fundamentals.c.period_end == period_end)
    )


def update_fundamentals(
    conn: sa.engine.Connection,
    tickers: list[str],
    config: dict,
    providers: Providers,
    refresh: bool = False,
) -> dict[str, int]:
    """Update fundamentals for all tickers (all of them if refresh). Returns {ticker: periods}."""
    merge_period_duplicates(conn)
    max_age_days = config.get("fundamentals", {}).get("refresh_days", 7)

    stale = [t for t in tickers if refresh or not _is_fresh(conn, t, max_age_days)]
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
