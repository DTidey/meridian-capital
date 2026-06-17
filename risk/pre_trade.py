"""Pre-trade risk gate — evaluates PENDING position_approvals and marks them APPROVED or REJECTED.

Eight checks are run in sequence. CLOSING trades (action in {SELL, COVER} or target_shares ≈ 0)
skip checks 2–8 and are subject only to the halt-lock check.
"""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import sqlalchemy as sa

from data.db import daily_prices, earnings_calendar
from factors.db import factor_scores as factor_scores_table
from portfolio.beta import compute_betas, portfolio_beta
from portfolio.db import portfolio_positions, position_approvals
from risk.db import risk_log
from risk.risk_state import is_halted

logger = logging.getLogger(__name__)

_CLOSING_ACTIONS = {"SELL", "COVER"}
_SHARE_ZERO_THRESHOLD = 1.0  # shares below this are treated as zero / full close


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_pre_trade(
    conn: sa.engine.Connection,
    score_date: str,
    config: dict,
    cache_dir: Path,
    whatif: bool = False,
) -> pd.DataFrame:
    """Evaluate all PENDING position_approvals for score_date.

    Returns DataFrame: ticker, action, result, reason.
    If whatif=True, does not write to DB.
    """
    cfg = _load_config(config)
    nav = float(config.get("portfolio", {}).get("nav_usd", 10_000_000))

    positions_df = _load_positions(conn)
    pending = _load_pending(conn, score_date)

    if pending.empty:
        logger.info("pre_trade: no PENDING rows for %s", score_date)
        return pd.DataFrame(columns=["ticker", "action", "result", "reason"])

    all_tickers = list(set(pending["ticker"].tolist()) | set(positions_df["ticker"].tolist()))
    latest_prices = _load_latest_prices(conn, all_tickers, score_date)
    adv_map = _load_adv(conn, all_tickers, score_date, cfg["adv_lookback"])
    sector_map = _load_sector_map(conn, score_date)
    blackout_tickers = _load_blackout_tickers(
        conn, pending["ticker"].tolist(), score_date, cfg["earnings_blackout_days"]
    )

    halted = is_halted(cache_dir)

    results = []
    approval_updates = []

    for _, row in pending.iterrows():
        result, reason, updated_row = _evaluate_trade(
            row=row,
            halted=halted,
            positions_df=positions_df,
            latest_prices=latest_prices,
            adv_map=adv_map,
            sector_map=sector_map,
            blackout_tickers=blackout_tickers,
            nav=nav,
            cfg=cfg,
            conn=conn,
            score_date=score_date,
        )
        results.append(
            {
                "ticker": row["ticker"],
                "action": row["action"],
                "result": result,
                "reason": reason,
            }
        )
        approval_updates.append((row["id"], result, reason, updated_row))

    if not whatif:
        _write_approval_updates(conn, approval_updates)
        _write_risk_log(conn, score_date, results)

    return pd.DataFrame(results)


# ---------------------------------------------------------------------------
# Per-trade evaluation
# ---------------------------------------------------------------------------


def _evaluate_trade(
    row: pd.Series,
    halted: bool,
    positions_df: pd.DataFrame,
    latest_prices: dict[str, float],
    adv_map: dict[str, float],
    sector_map: dict[str, str],
    blackout_tickers: set[str],
    nav: float,
    cfg: dict,
    conn: sa.engine.Connection,
    score_date: str,
) -> tuple[str, str, dict | None]:
    """Return (result, reason, updated_row_dict_or_None)."""

    ticker = row["ticker"]
    action = row["action"]
    target_shares = float(row["target_shares"] or 0.0)
    current_shares = float(row["current_shares"] or 0.0)
    delta_shares = float(row["delta_shares"] or 0.0)

    is_closing = action in _CLOSING_ACTIONS or abs(target_shares) < _SHARE_ZERO_THRESHOLD

    # Closing trades always pass — needed to exit positions under halt or stress
    if is_closing:
        return "APPROVED", "CLOSING trade — skipped risk checks", None

    # Check 1 — halt lock (opening trades only)
    if halted:
        return "REJECTED", "HALT_LOCK: trading halted", None

    price = latest_prices.get(ticker)
    if price is None or price <= 0:
        logger.warning("pre_trade: no price for %s — skipping size/liquidity checks", ticker)
        return "APPROVED", f"NO_PRICE_DATA: approved with warning for {ticker}", None

    # Check 2 — earnings blackout (resize to 50%, do not reject)
    if ticker in blackout_tickers:
        new_target = round(target_shares * 0.50)
        new_delta = new_target - current_shares
        updated_row = {
            "target_shares": new_target,
            "delta_shares": new_delta,
            "status": "APPROVED",
        }
        reason = f"BLACKOUT_REDUCED: earnings within {cfg['earnings_blackout_days']}d — target resized to 50%"
        return "APPROVED", reason, updated_row

    # Check 3 — liquidity
    adv = adv_map.get(ticker, 0.0)
    if adv > 0:
        trade_value = abs(delta_shares) * price
        adv_limit = cfg["adv_pct"] * adv
        if trade_value > adv_limit:
            return (
                "REJECTED",
                f"LIQUIDITY: trade ${trade_value:,.0f} > {cfg['adv_pct'] * 100:.0f}% ADV ${adv_limit:,.0f}",
                None,
            )

    # Check 4 — position size
    position_value = abs(target_shares * price)
    position_pct = position_value / nav if nav > 0 else 0.0
    if position_pct > cfg["max_position_pct"]:
        return (
            "REJECTED",
            f"POSITION_SIZE: {position_pct:.1%} > max {cfg['max_position_pct']:.1%}",
            None,
        )

    # Build pro-forma weights for checks 5–7
    proforma_weights = _build_proforma_weights(
        ticker, action, target_shares, price, nav, positions_df
    )

    # Check 5 — sector concentration
    sector = sector_map.get(ticker)
    reject, reason = _check_sector_concentration(
        proforma_weights, sector_map, sector, cfg["max_sector_pct"]
    )
    if reject:
        return "REJECTED", reason, None

    # Check 6 — gross / net exposure
    gross = proforma_weights.abs().sum()
    net = proforma_weights.sum()
    if gross > cfg["max_gross"]:
        return (
            "REJECTED",
            f"GROSS_EXPOSURE: pro-forma gross {gross:.2f} > max {cfg['max_gross']:.2f}",
            None,
        )
    if not (cfg["net_min"] <= net <= cfg["net_max"]):
        return (
            "REJECTED",
            f"NET_EXPOSURE: pro-forma net {net:.3f} outside [{cfg['net_min']:.2f}, {cfg['net_max']:.2f}]",
            None,
        )

    # Check 7 — net beta
    all_tickers_for_beta = proforma_weights.index.tolist()
    betas = compute_betas(conn, all_tickers_for_beta, score_date, lookback_days=60)
    net_beta = portfolio_beta(proforma_weights, betas)
    if abs(net_beta) > cfg["max_net_beta"]:
        return (
            "REJECTED",
            f"NET_BETA: pro-forma |net_beta| {abs(net_beta):.3f} > max {cfg['max_net_beta']:.3f}",
            None,
        )

    # Check 8 — pairwise correlation (new positions only)
    is_new = abs(current_shares) < _SHARE_ZERO_THRESHOLD
    if is_new:
        reject, reason = _check_pairwise_corr(
            conn=conn,
            new_ticker=ticker,
            action=action,
            positions_df=positions_df,
            score_date=score_date,
            lookback=cfg["corr_lookback"],
            max_corr=cfg["max_pairwise_corr"],
        )
        if reject:
            return "REJECTED", reason, None

    return "APPROVED", "All checks passed", None


# ---------------------------------------------------------------------------
# Individual check helpers
# ---------------------------------------------------------------------------


def _check_sector_concentration(
    proforma_weights: pd.Series,
    sector_map: dict[str, str],
    new_ticker_sector: str | None,
    max_sector_pct: float,
) -> tuple[bool, str]:
    """Return (reject, reason). Gross sector exposure = sum of abs weights in sector."""
    if not new_ticker_sector:
        return False, ""

    sector_gross: dict[str, float] = {}
    for tkr, wt in proforma_weights.items():
        sec = sector_map.get(tkr)
        if sec:
            sector_gross[sec] = sector_gross.get(sec, 0.0) + abs(wt)

    for sec, gross_exp in sector_gross.items():
        if gross_exp > max_sector_pct:
            return (
                True,
                f"SECTOR_CONCENTRATION: {sec} pro-forma gross {gross_exp:.1%} > max {max_sector_pct:.1%}",
            )
    return False, ""


def _check_pairwise_corr(
    conn: sa.engine.Connection,
    new_ticker: str,
    action: str,
    positions_df: pd.DataFrame,
    score_date: str,
    lookback: int,
    max_corr: float,
) -> tuple[bool, str]:
    """Return (reject, reason) based on 60d rolling correlation with same-book peers."""
    new_book = "LONG" if action in {"BUY"} else "SHORT"

    same_book = (
        positions_df[positions_df["direction"] == new_book]["ticker"].tolist()
        if not positions_df.empty and "direction" in positions_df.columns
        else []
    )

    if not same_book:
        return False, ""

    tickers_to_load = [new_ticker] + same_book
    rows = conn.execute(
        sa.select(
            daily_prices.c.ticker,
            daily_prices.c.date,
            daily_prices.c.adj_close,
        )
        .where(daily_prices.c.ticker.in_(tickers_to_load) & (daily_prices.c.date <= score_date))
        .order_by(daily_prices.c.date.asc())
    ).fetchall()

    if not rows:
        return False, ""

    prices = (
        pd.DataFrame(rows, columns=["ticker", "date", "adj_close"])
        .pivot(index="date", columns="ticker", values="adj_close")
        .tail(lookback + 1)
    )
    returns = np.log(prices / prices.shift(1)).dropna()

    if new_ticker not in returns.columns:
        return False, ""

    new_ret = returns[new_ticker].dropna()

    for peer in same_book:
        if peer not in returns.columns:
            continue
        peer_ret = returns[peer].dropna()
        overlap = new_ret.index.intersection(peer_ret.index)
        if len(overlap) < 20:
            continue
        corr = new_ret.loc[overlap].corr(peer_ret.loc[overlap])
        if pd.notna(corr) and corr > max_corr:
            return (
                True,
                f"PAIRWISE_CORR: {new_ticker} vs {peer} corr={corr:.3f} > max {max_corr:.2f}",
            )

    return False, ""


# ---------------------------------------------------------------------------
# Pro-forma weight builder
# ---------------------------------------------------------------------------


def _build_proforma_weights(
    ticker: str,
    action: str,
    target_shares: float,
    price: float,
    nav: float,
    positions_df: pd.DataFrame,
) -> pd.Series:
    """Return signed weight series after applying the proposed trade."""
    weights: dict[str, float] = {}

    if not positions_df.empty and "ticker" in positions_df.columns:
        for _, pos in positions_df.iterrows():
            t = pos["ticker"]
            direction = pos.get("direction", "LONG")
            w = float(pos.get("weight", 0.0))
            # Ensure sign convention: LONG positive, SHORT negative
            weights[t] = abs(w) if direction == "LONG" else -abs(w)

    new_weight = target_shares * price / nav if nav > 0 else 0.0
    if action in {"SHORT"}:
        new_weight = -abs(new_weight)
    elif action in {"BUY"}:
        new_weight = abs(new_weight)

    weights[ticker] = new_weight
    return pd.Series(weights)


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------


def _load_config(config: dict) -> dict:
    pt = config.get("risk", {}).get("pre_trade", {})
    return {
        "adv_lookback": int(pt.get("adv_lookback", 20)),
        "adv_pct": float(pt.get("adv_pct", 0.05)),
        "max_position_pct": float(pt.get("max_position_pct", 0.05)),
        "max_sector_pct": float(pt.get("max_sector_pct", 0.25)),
        "max_gross": float(pt.get("max_gross", 1.65)),
        "net_min": float(pt.get("net_min", -0.10)),
        "net_max": float(pt.get("net_max", 0.15)),
        "max_net_beta": float(pt.get("max_net_beta", 0.20)),
        "max_pairwise_corr": float(pt.get("max_pairwise_corr", 0.80)),
        "corr_lookback": int(pt.get("corr_lookback", 60)),
        "earnings_blackout_days": int(pt.get("earnings_blackout_days", 5)),
    }


def _load_positions(conn: sa.engine.Connection) -> pd.DataFrame:
    rows = conn.execute(sa.select(portfolio_positions)).fetchall()
    cols = [c.name for c in portfolio_positions.columns]
    return pd.DataFrame(rows, columns=cols)


def _load_pending(conn: sa.engine.Connection, score_date: str) -> pd.DataFrame:
    rows = conn.execute(
        sa.select(position_approvals).where(
            (position_approvals.c.rebalance_date == score_date)
            & (position_approvals.c.status == "PENDING")
        )
    ).fetchall()
    cols = [c.name for c in position_approvals.columns]
    return pd.DataFrame(rows, columns=cols)


def _load_latest_prices(
    conn: sa.engine.Connection,
    tickers: list[str],
    score_date: str,
) -> dict[str, float]:
    """Return {ticker: latest_close} for each ticker, on or before score_date."""
    if not tickers:
        return {}

    rows = conn.execute(
        sa.select(
            daily_prices.c.ticker,
            sa.func.max(daily_prices.c.date).label("max_date"),
        )
        .where(daily_prices.c.ticker.in_(tickers) & (daily_prices.c.date <= score_date))
        .group_by(daily_prices.c.ticker)
    ).fetchall()

    if not rows:
        return {}

    ticker_max_date = {r[0]: r[1] for r in rows}

    price_map: dict[str, float] = {}
    for ticker, max_date in ticker_max_date.items():
        price_row = conn.execute(
            sa.select(daily_prices.c.close).where(
                (daily_prices.c.ticker == ticker) & (daily_prices.c.date == max_date)
            )
        ).fetchone()
        if price_row and price_row[0]:
            price_map[ticker] = float(price_row[0])

    return price_map


def _load_adv(
    conn: sa.engine.Connection,
    tickers: list[str],
    score_date: str,
    lookback: int,
) -> dict[str, float]:
    """Return {ticker: 20d average daily value} = mean(close * volume)."""
    if not tickers:
        return {}

    rows = conn.execute(
        sa.select(
            daily_prices.c.ticker,
            daily_prices.c.close,
            daily_prices.c.volume,
        )
        .where(daily_prices.c.ticker.in_(tickers) & (daily_prices.c.date <= score_date))
        .order_by(daily_prices.c.date.desc())
    ).fetchall()

    if not rows:
        return {}

    df = pd.DataFrame(rows, columns=["ticker", "close", "volume"])
    df = df.dropna(subset=["close", "volume"])
    df["dv"] = df["close"] * df["volume"]

    adv: dict[str, float] = {}
    for ticker, grp in df.groupby("ticker"):
        adv[ticker] = float(grp["dv"].head(lookback).mean())

    return adv


def _load_sector_map(conn: sa.engine.Connection, score_date: str) -> dict[str, str]:
    """Return {ticker: sector} from the latest factor_scores row on score_date."""
    rows = conn.execute(
        sa.select(factor_scores_table.c.ticker, factor_scores_table.c.sector).where(
            factor_scores_table.c.score_date == score_date
        )
    ).fetchall()
    return {r[0]: r[1] for r in rows if r[1]}


def _load_blackout_tickers(
    conn: sa.engine.Connection,
    tickers: list[str],
    score_date: str,
    blackout_days: int,
) -> set[str]:
    """Return tickers with earnings within ±blackout_days of score_date."""
    if not tickers:
        return set()

    ref_date = datetime.fromisoformat(score_date).date()
    lo = (ref_date - timedelta(days=blackout_days)).isoformat()
    hi = (ref_date + timedelta(days=blackout_days)).isoformat()

    rows = conn.execute(
        sa.select(earnings_calendar.c.ticker).where(
            earnings_calendar.c.ticker.in_(tickers)
            & (earnings_calendar.c.earnings_date >= lo)
            & (earnings_calendar.c.earnings_date <= hi)
        )
    ).fetchall()

    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# DB write helpers
# ---------------------------------------------------------------------------


def _write_approval_updates(
    conn: sa.engine.Connection,
    updates: list[tuple],
) -> None:
    """Update position_approvals rows: (id, result, reason, updated_row_dict|None)."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    for approval_id, result, _reason, updated_row in updates:
        status = "APPROVED" if result == "APPROVED" else "REJECTED"
        stmt_vals: dict = {
            "status": status,
            "reviewed_at": now,
        }
        if updated_row:
            stmt_vals.update(updated_row)

        conn.execute(
            position_approvals.update()
            .where(position_approvals.c.id == approval_id)
            .values(**stmt_vals)
        )
    conn.commit()
    logger.info("pre_trade: updated %d approval rows", len(updates))


def _write_risk_log(
    conn: sa.engine.Connection,
    score_date: str,
    results: list[dict],
) -> None:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    rows = [
        {
            "run_date": score_date,
            "check_type": "pre_trade",
            "ticker": r["ticker"],
            "result": r["result"],
            "reason": r["reason"],
            "recorded_at": now,
        }
        for r in results
    ]
    if rows:
        conn.execute(risk_log.insert(), rows)
        conn.commit()
    logger.info("pre_trade: wrote %d risk_log rows", len(rows))
