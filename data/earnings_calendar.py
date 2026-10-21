"""Earnings calendar — upcoming dates for the next N days via yfinance."""

import contextlib
import logging
from datetime import timedelta

import pandas as pd
import sqlalchemy as sa
import yfinance as yf

from data._utils import utc_now

from .db import earnings_calendar, insert_or_replace
from .yahoo_info import call_with_retry, fetch_in_two_passes, log_coverage

logger = logging.getLogger(__name__)


def _fetch_earnings_date(ticker: str) -> list[dict] | None:
    """Upcoming earnings dates for one ticker; None when the fetch failed, [] when it had none."""
    try:
        cal = call_with_retry(lambda: yf.Ticker(ticker).calendar)
    except Exception as exc:
        logger.debug("Earnings calendar fetch failed %s: %s", ticker, exc)
        return None
    try:
        return _parse_calendar(cal)
    except Exception as exc:
        logger.debug("Earnings calendar parse failed %s: %s", ticker, exc)
        return None


def _parse_calendar(cal) -> list[dict]:
    if cal is None:
        return []

    if isinstance(cal, pd.DataFrame):
        if "Earnings Date" in cal.index:
            dates = cal.loc["Earnings Date"].values
        else:
            return []
    elif isinstance(cal, dict):
        dates = cal.get("Earnings Date", [])
        if not isinstance(dates, (list, tuple)):
            dates = [dates]
    else:
        return []

    eps_est = _parse_eps_estimate(cal)

    results = []
    for d in dates:
        if d is None:
            continue
        try:
            if hasattr(d, "strftime"):
                date_str = d.strftime("%Y-%m-%d")
            else:
                date_str = str(d)[:10]
            results.append({"earnings_date": date_str, "eps_estimate": eps_est})
        except Exception:  # nosec B112
            continue
    return results


def _parse_eps_estimate(cal) -> float | None:
    eps_est = None
    if isinstance(cal, pd.DataFrame):
        key = next((k for k in ("Earnings Average", "Earnings Estimate") if k in cal.index), None)
        if key:
            with contextlib.suppress(TypeError, ValueError, IndexError):
                eps_est = float(cal.loc[key].iloc[0])
    elif isinstance(cal, dict):
        val = cal.get("Earnings Average")
        if val is not None:
            with contextlib.suppress(TypeError, ValueError):
                eps_est = float(val[0] if isinstance(val, (list, tuple)) else val)
    return eps_est


def _store_entries(conn, ticker: str, entries: list[dict], window: tuple[str, str], now: str):
    """Insert the in-window entries; return (rows stored, in-window dates)."""
    today, cutoff = window
    stored = 0
    dates: set[str] = set()
    for entry in entries:
        ed = entry["earnings_date"]
        if ed < today or ed > cutoff:
            continue
        dates.add(ed)
        try:
            conn.execute(
                insert_or_replace(conn, earnings_calendar).values(
                    ticker=ticker,
                    earnings_date=ed,
                    eps_estimate=entry["eps_estimate"],
                    fetched_at=now,
                )
            )
            stored += 1
        except Exception as exc:
            logger.debug("Earnings calendar insert error %s: %s", ticker, exc)
    return stored, dates


def _delete_superseded(conn, ticker: str, keep: set[str], today: str) -> int:
    """Delete the ticker's future dates not in keep; past dates are never touched."""
    result = conn.execute(
        sa.delete(earnings_calendar).where(
            (earnings_calendar.c.ticker == ticker)
            & (earnings_calendar.c.earnings_date >= today)
            & earnings_calendar.c.earnings_date.not_in(sorted(keep))
        )
    )
    return result.rowcount or 0


def update_earnings_calendar(
    conn: sa.engine.Connection,
    tickers: list[str],
    config: dict,
) -> dict[str, int]:
    """Refresh upcoming earnings dates for all universe tickers."""
    lookahead = config["earnings_calendar"]["lookahead_days"]
    today = utc_now().strftime("%Y-%m-%d")
    cutoff = (utc_now() + timedelta(days=lookahead)).strftime("%Y-%m-%d")
    now = utc_now().isoformat(timespec="seconds")

    already = set(
        conn.execute(
            sa.select(earnings_calendar.c.ticker)
            .distinct()
            .where(earnings_calendar.c.fetched_at >= today)
        ).scalars()
    )
    to_fetch = [t for t in tickers if t not in already]
    logger.info(
        "Earnings calendar: fetching %d tickers (%d already updated today)",
        len(to_fetch),
        len(already),
    )

    summary: dict[str, int] = {t: 0 for t in tickers if t in already}
    removed = 0

    def fetch_one(ticker: str) -> bool:
        nonlocal removed
        entries = _fetch_earnings_date(ticker)
        if entries is None:
            summary[ticker] = 0
            return False
        stored, dates = _store_entries(conn, ticker, entries, (today, cutoff), now)
        if stored:
            removed += _delete_superseded(conn, ticker, dates, today)
        summary[ticker] = stored
        return True

    fetched = fetch_in_two_passes(to_fetch, fetch_one)
    conn.commit()
    total = sum(summary.values())
    log_coverage(
        "Earnings calendar",
        fetched,
        len(to_fetch),
        f", {total} upcoming events stored, {removed} superseded removed",
    )
    return summary
