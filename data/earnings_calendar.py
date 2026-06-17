"""Earnings calendar — upcoming dates for the next N days via yfinance."""

import contextlib
import logging
from datetime import datetime, timedelta

import pandas as pd
import sqlalchemy as sa
import yfinance as yf

from .db import earnings_calendar, insert_or_replace

logger = logging.getLogger(__name__)


def _fetch_earnings_date(ticker: str) -> list[dict]:
    """Return upcoming earnings dates for a single ticker."""
    try:
        t = yf.Ticker(ticker)
        cal = t.calendar
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
            except Exception:
                continue
        return results

    except Exception as exc:
        logger.debug("Earnings calendar fetch failed %s: %s", ticker, exc)
        return []


def update_earnings_calendar(
    conn: sa.engine.Connection,
    tickers: list[str],
    config: dict,
) -> dict[str, int]:
    """Refresh upcoming earnings dates for all universe tickers."""
    lookahead = config["earnings_calendar"]["lookahead_days"]
    today     = datetime.utcnow().strftime("%Y-%m-%d")
    cutoff    = (datetime.utcnow() + timedelta(days=lookahead)).strftime("%Y-%m-%d")
    now       = datetime.utcnow().isoformat(timespec="seconds")

    already = set(conn.execute(
        sa.select(earnings_calendar.c.ticker).distinct()
        .where(earnings_calendar.c.fetched_at >= today)
    ).scalars())
    to_fetch = [t for t in tickers if t not in already]
    logger.info(
        "Earnings calendar: fetching %d tickers (%d already updated today)",
        len(to_fetch), len(already),
    )

    summary: dict[str, int] = {t: 0 for t in tickers if t in already}

    for ticker in to_fetch:
        entries = _fetch_earnings_date(ticker)
        stored  = 0
        for entry in entries:
            ed = entry["earnings_date"]
            if ed < today or ed > cutoff:
                continue
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
        summary[ticker] = stored

    conn.commit()
    total = sum(summary.values())
    logger.info("Earnings calendar complete — %d upcoming events stored", total)
    return summary
