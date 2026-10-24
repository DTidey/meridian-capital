"""Rebuild portfolio_positions from Alpaca, the book of record, and snapshot it."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import sqlalchemy as sa

from analysis.db import combined_scores
from data.db import daily_prices, sp500_universe
from execution.symbols import from_alpaca
from portfolio.beta import compute_betas
from portfolio.db import portfolio_history, portfolio_positions

log = logging.getLogger(__name__)

_SHARE_TOLERANCE = 0.5


def sync_book(conn, client, score_date: str, dry_run: bool = False) -> dict:
    """Replace portfolio_positions with the broker's positions; snapshot them for score_date.

    Shares, market value and weight are signed (shorts negative). Commits (or rolls back) the
    caller's open transaction on conn. Returns
    {"positions": n, "added": [...], "removed": [...], "changed": [...]}.
    """
    broker = _broker_book(client)
    equity = float(client.get_account().equity)
    existing = {r.ticker: r for r in conn.execute(sa.select(portfolio_positions)).fetchall()}
    summary = _diff(existing, broker)

    if dry_run:
        log.info("Book sync (dry run) %s: %s", score_date, summary)
        return summary

    rows = _enrich(conn, broker, existing, score_date, equity)
    try:
        conn.execute(portfolio_positions.delete())
        if rows:
            conn.execute(portfolio_positions.insert(), rows)
        conn.execute(
            portfolio_history.delete().where(portfolio_history.c.snapshot_date == score_date)
        )
        if rows:
            conn.execute(portfolio_history.insert(), [_history_row(r, score_date) for r in rows])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    log.info("Book sync %s: %s", score_date, summary)
    return summary


def _broker_book(client) -> dict[str, dict]:
    """{db ticker: {shares (signed), entry_price, current_price}} from Alpaca positions."""
    book: dict[str, dict] = {}
    for p in client.get_all_positions():
        qty = abs(float(p.qty))
        side = str(getattr(p.side, "value", p.side)).lower()
        book[from_alpaca(p.symbol)] = {
            "shares": -qty if side == "short" else qty,
            "entry_price": float(p.avg_entry_price),
            "current_price": float(p.current_price) if p.current_price else None,
        }
    return book


def _diff(existing: dict, broker: dict[str, dict]) -> dict:
    changed = [
        t
        for t in sorted(set(existing) & set(broker))
        if abs(float(existing[t].shares or 0.0) - broker[t]["shares"]) > _SHARE_TOLERANCE
    ]
    return {
        "positions": len(broker),
        "added": sorted(set(broker) - set(existing)),
        "removed": sorted(set(existing) - set(broker)),
        "changed": changed,
    }


def _enrich(conn, broker: dict[str, dict], existing: dict, score_date: str, equity: float):
    """portfolio_positions rows for the broker book, with metadata from our tables."""
    tickers = sorted(broker)
    if not tickers:
        return []
    sectors = _sectors(conn, tickers)
    scores = _latest_scores(conn, tickers, score_date)
    closes = _latest_closes(conn, tickers, score_date)
    betas = compute_betas(conn, tickers, score_date)
    now = datetime.now(UTC).isoformat(timespec="seconds")

    rows = []
    for t in tickers:
        b = broker[t]
        shares = b["shares"]
        price = b["current_price"] or closes.get(t) or b["entry_price"]
        market_value = shares * price
        direction = "SHORT" if shares < 0 else "LONG"
        prior = existing.get(t)
        held = prior is not None and prior.direction == direction and prior.entry_date
        rows.append(
            {
                "ticker": t,
                "direction": direction,
                "shares": shares,
                "entry_price": b["entry_price"],
                "entry_date": prior.entry_date if held else score_date,
                "current_price": price,
                "market_value": market_value,
                "weight": market_value / equity if equity > 0 else None,
                "unrealized_pnl": (price - b["entry_price"]) * shares,
                "sector": sectors.get(t),
                "combined_score": scores.get(t),
                "beta": float(betas.get(t, 1.0)),
                "updated_at": now,
            }
        )
    return rows


def _history_row(row: dict, score_date: str) -> dict:
    return {
        "snapshot_date": score_date,
        "ticker": row["ticker"],
        "direction": row["direction"],
        "shares": row["shares"],
        "price": row["current_price"],
        "market_value": row["market_value"],
        "weight": row["weight"],
        "unrealized_pnl": row["unrealized_pnl"],
        "sector": row["sector"],
        "combined_score": row["combined_score"],
        "recorded_at": row["updated_at"],
    }


def _sectors(conn, tickers: list[str]) -> dict[str, str]:
    rows = conn.execute(
        sa.select(sp500_universe.c.ticker, sp500_universe.c.gics_sector).where(
            sp500_universe.c.ticker.in_(tickers)
        )
    ).fetchall()
    return dict(rows)


def _latest_scores(conn, tickers: list[str], score_date: str) -> dict[str, float]:
    rows = conn.execute(
        sa.select(combined_scores.c.ticker, combined_scores.c.combined_score)
        .where(combined_scores.c.ticker.in_(tickers) & (combined_scores.c.score_date <= score_date))
        .order_by(combined_scores.c.score_date)
    ).fetchall()
    return dict(rows)  # ascending dates, so the latest wins


def _latest_closes(conn, tickers: list[str], score_date: str) -> dict[str, float]:
    rows = conn.execute(
        sa.select(daily_prices.c.ticker, daily_prices.c.close)
        .where(
            daily_prices.c.ticker.in_(tickers)
            & (daily_prices.c.date <= score_date)
            & daily_prices.c.close.isnot(None)
        )
        .order_by(daily_prices.c.date)
    ).fetchall()
    return {t: float(c) for t, c in rows}
