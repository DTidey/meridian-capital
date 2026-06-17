"""Circuit breaker module — monitors P&L and drawdown, modifies position_approvals.

Triggers are evaluated in priority order (most severe first):
  1. KILL_SWITCH   — drawdown > 8%
  2. CLOSE_ALL     — daily P&L < -2.5%
  3. SIZE_DOWN_30  — daily P&L < -1.5% (or weekly < -4%)
  4. FORCE_CLOSE   — individual LONG position > max_single_position_pct of NAV

At most one SIZE_DOWN action fires; KILL_SWITCH overrides all.
FORCE_CLOSE checks are independent and always evaluated.
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import sqlalchemy as sa

from portfolio.db import portfolio_history, portfolio_positions, position_approvals
from risk.db import risk_events, risk_log
from risk.risk_state import set_halt

logger = logging.getLogger(__name__)

_CLOSING_ACTIONS = {"SELL", "COVER"}
_SHARE_ZERO_THRESHOLD = 1.0


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_circuit_breakers(
    conn: sa.engine.Connection,
    score_date: str,
    nav_usd: float,
    config: dict,
    risk_state: dict,
    cache_dir: Path,
    whatif: bool = False,
) -> dict:
    """Evaluate circuit breakers and apply interventions to position_approvals.

    Returns updated risk_state dict with circuit_breaker_state, P&L fields,
    drawdown, and peak_nav_usd.
    """
    cfg = _load_config(config)

    daily_pnl_pct, weekly_pnl_pct, drawdown_pct, peak_nav_usd = _compute_pnl(
        conn, score_date, nav_usd, risk_state
    )

    today_nav = nav_usd * (1.0 + daily_pnl_pct)
    daily_pnl_usd = today_nav - nav_usd

    risk_state = dict(risk_state)
    risk_state["nav_usd"] = nav_usd
    risk_state["daily_pnl_usd"] = daily_pnl_usd
    risk_state["daily_pnl_pct"] = daily_pnl_pct
    risk_state["weekly_pnl_pct"] = weekly_pnl_pct
    risk_state["drawdown_pct"] = drawdown_pct
    risk_state["peak_nav_usd"] = peak_nav_usd

    cb_state = "NORMAL"
    size_down_fired = False

    # -----------------------------------------------------------------------
    # Trigger 1 — KILL_SWITCH
    # -----------------------------------------------------------------------
    if drawdown_pct > cfg["drawdown_kill"]:
        logger.warning(
            "KILL_SWITCH triggered: drawdown %.2f%% > threshold %.2f%%",
            drawdown_pct * 100,
            cfg["drawdown_kill"] * 100,
        )
        if not whatif:
            set_halt(cache_dir)
            count = _apply_close_all(conn, score_date, whatif=False)
            _log_event(
                conn,
                score_date,
                "KILL_SWITCH",
                "drawdown",
                {
                    "drawdown_pct": round(drawdown_pct, 6),
                    "peak_nav_usd": round(peak_nav_usd, 2),
                    "nav_usd": round(nav_usd, 2),
                    "rejected_count": count,
                },
            )
            _log_check(
                conn,
                score_date,
                "circuit_breaker",
                None,
                "TRIGGERED",
                f"KILL_SWITCH: drawdown {drawdown_pct:.4%} > {cfg['drawdown_kill']:.4%}",
            )
        cb_state = "KILL_SWITCH"

    # -----------------------------------------------------------------------
    # Trigger 2 — CLOSE_ALL
    # -----------------------------------------------------------------------
    elif daily_pnl_pct < -cfg["daily_close_all"]:
        logger.warning(
            "CLOSE_ALL triggered: daily P&L %.2f%% < -%.2f%%",
            daily_pnl_pct * 100,
            cfg["daily_close_all"] * 100,
        )
        if not whatif:
            count = _apply_close_all(conn, score_date, whatif=False)
            _log_event(
                conn,
                score_date,
                "CLOSE_ALL",
                "daily_pnl",
                {
                    "daily_pnl_pct": round(daily_pnl_pct, 6),
                    "nav_usd": round(nav_usd, 2),
                    "rejected_count": count,
                },
            )
            _log_check(
                conn,
                score_date,
                "circuit_breaker",
                None,
                "TRIGGERED",
                f"CLOSE_ALL: daily_pnl {daily_pnl_pct:.4%} < -{cfg['daily_close_all']:.4%}",
            )
        cb_state = "CLOSE_ALL"

    # -----------------------------------------------------------------------
    # Trigger 3 — SIZE_DOWN_30 (daily)
    # -----------------------------------------------------------------------
    elif daily_pnl_pct < -cfg["daily_size_down"]:
        logger.warning(
            "SIZE_DOWN_30 triggered (daily): daily P&L %.2f%% < -%.2f%%",
            daily_pnl_pct * 100,
            cfg["daily_size_down"] * 100,
        )
        if not whatif:
            count = _apply_size_down(conn, score_date, factor=0.70, whatif=False)
            _log_event(
                conn,
                score_date,
                "SIZE_DOWN_30",
                "daily_pnl",
                {
                    "daily_pnl_pct": round(daily_pnl_pct, 6),
                    "nav_usd": round(nav_usd, 2),
                    "modified_count": count,
                },
            )
            _log_check(
                conn,
                score_date,
                "circuit_breaker",
                None,
                "TRIGGERED",
                f"SIZE_DOWN_30: daily_pnl {daily_pnl_pct:.4%} < -{cfg['daily_size_down']:.4%}",
            )
        cb_state = "SIZE_DOWN"
        size_down_fired = True

    # -----------------------------------------------------------------------
    # Trigger 4 — SIZE_DOWN_30 (weekly), only if not already fired
    # -----------------------------------------------------------------------
    if not size_down_fired and cb_state == "NORMAL" and weekly_pnl_pct < -cfg["weekly_size_down"]:
        logger.warning(
            "SIZE_DOWN_30 triggered (weekly): weekly P&L %.2f%% < -%.2f%%",
            weekly_pnl_pct * 100,
            cfg["weekly_size_down"] * 100,
        )
        if not whatif:
            count = _apply_size_down(conn, score_date, factor=0.70, whatif=False)
            _log_event(
                conn,
                score_date,
                "SIZE_DOWN_30",
                "weekly_pnl",
                {
                    "weekly_pnl_pct": round(weekly_pnl_pct, 6),
                    "nav_usd": round(nav_usd, 2),
                    "modified_count": count,
                },
            )
            _log_check(
                conn,
                score_date,
                "circuit_breaker",
                None,
                "TRIGGERED",
                f"SIZE_DOWN_30: weekly_pnl {weekly_pnl_pct:.4%} < -{cfg['weekly_size_down']:.4%}",
            )
        cb_state = "SIZE_DOWN"

    # -----------------------------------------------------------------------
    # Trigger 5 — FORCE_CLOSE oversized individual positions
    # -----------------------------------------------------------------------
    if nav_usd > 0:
        positions = _load_positions(conn)
        for _, row in positions.iterrows():
            ticker = row["ticker"]
            direction = str(row.get("direction", "LONG")).upper()
            mv = float(row.get("market_value", 0.0) or 0.0)
            pos_pct = abs(mv) / nav_usd

            if pos_pct > cfg["max_single_position_pct"]:
                logger.warning(
                    "FORCE_CLOSE triggered for %s: position %.2f%% > max %.2f%%",
                    ticker,
                    pos_pct * 100,
                    cfg["max_single_position_pct"] * 100,
                )
                if not whatif:
                    _apply_force_close(conn, score_date, ticker, direction)
                    _log_event(
                        conn,
                        score_date,
                        "FORCE_CLOSE",
                        "position_size",
                        {
                            "ticker": ticker,
                            "position_pct": round(pos_pct, 6),
                            "market_value": round(mv, 2),
                            "max_single_position_pct": cfg["max_single_position_pct"],
                        },
                    )
                    _log_check(
                        conn,
                        score_date,
                        "circuit_breaker",
                        ticker,
                        "TRIGGERED",
                        f"FORCE_CLOSE: {ticker} position {pos_pct:.4%} > "
                        f"{cfg['max_single_position_pct']:.4%}",
                    )

    risk_state["circuit_breaker_state"] = cb_state
    logger.info(
        "circuit_breakers: %s | daily=%.2f%% weekly=%.2f%% drawdown=%.2f%%",
        cb_state,
        daily_pnl_pct * 100,
        weekly_pnl_pct * 100,
        drawdown_pct * 100,
    )
    return risk_state


# ---------------------------------------------------------------------------
# P&L computation
# ---------------------------------------------------------------------------


def _compute_pnl(
    conn: sa.engine.Connection,
    score_date: str,
    nav_usd: float,
    risk_state: dict,
) -> tuple[float, float, float, float]:
    """Return (daily_pnl_pct, weekly_pnl_pct, drawdown_pct, peak_nav_usd)."""

    today_nav = _nav_from_history(conn, score_date, nav_usd)

    if today_nav is None:
        # Fall back to portfolio_positions (nav_usd + unrealized P&L)
        today_nav = _nav_from_positions(conn, nav_usd)

    if today_nav is None or today_nav == 0.0:
        # No data at all — all metrics are zero, no triggers fire
        peak_nav = float(risk_state.get("peak_nav_usd", nav_usd) or nav_usd)
        peak_nav = max(peak_nav, nav_usd)
        return 0.0, 0.0, 0.0, peak_nav

    # Daily P&L
    yesterday_nav = _latest_nav_before(conn, score_date, nav_usd)
    if yesterday_nav is not None and nav_usd > 0:
        daily_pnl_pct = (today_nav - yesterday_nav) / nav_usd
    else:
        daily_pnl_pct = 0.0

    # Weekly P&L
    week_ago_nav = _nav_before_days_ago(conn, score_date, days=7, nav_usd=nav_usd)
    if week_ago_nav is not None and nav_usd > 0:
        weekly_pnl_pct = (today_nav - week_ago_nav) / nav_usd
    else:
        weekly_pnl_pct = daily_pnl_pct

    # Peak NAV and drawdown
    peak_nav = max(
        float(risk_state.get("peak_nav_usd", nav_usd) or nav_usd),
        today_nav,
        nav_usd,
    )
    drawdown_pct = max((peak_nav - today_nav) / peak_nav, 0.0) if peak_nav > 0 else 0.0

    return daily_pnl_pct, weekly_pnl_pct, drawdown_pct, peak_nav


def _total_unrealised_pnl_from_history(
    conn: sa.engine.Connection, snapshot_date: str
) -> float | None:
    """Return sum(unrealized_pnl) for a given portfolio_history snapshot, or None."""
    rows = conn.execute(
        sa.select(portfolio_history.c.unrealized_pnl).where(
            portfolio_history.c.snapshot_date == snapshot_date
        )
    ).fetchall()
    if not rows:
        return None
    return sum(float(r[0] or 0.0) for r in rows)


def _nav_from_history(
    conn: sa.engine.Connection, snapshot_date: str, nav_usd: float
) -> float | None:
    """Return estimated NAV = nav_usd + sum(unrealized_pnl) for snapshot_date, or None."""
    pnl = _total_unrealised_pnl_from_history(conn, snapshot_date)
    return (nav_usd + pnl) if pnl is not None else None


def _nav_from_positions(conn: sa.engine.Connection, nav_usd: float) -> float | None:
    """Estimate NAV from portfolio_positions as nav_usd + sum(unrealized_pnl).

    This avoids the net-long-minus-short distortion that occurs when gross exposure
    is much larger than net exposure. Returns None if no positions exist.
    """
    rows = conn.execute(sa.select(portfolio_positions.c.unrealized_pnl)).fetchall()

    if not rows:
        return None
    total_pnl = sum(float(r[0] or 0.0) for r in rows)
    return nav_usd + total_pnl


def _latest_nav_before(conn: sa.engine.Connection, score_date: str, nav_usd: float) -> float | None:
    """Return NAV for the most recent snapshot_date strictly before score_date."""
    row = conn.execute(
        sa.select(sa.func.max(portfolio_history.c.snapshot_date)).where(
            portfolio_history.c.snapshot_date < score_date
        )
    ).scalar()

    if row is None:
        return None
    return _nav_from_history(conn, row, nav_usd)


def _nav_before_days_ago(
    conn: sa.engine.Connection, score_date: str, days: int, nav_usd: float
) -> float | None:
    """Return NAV for most recent snapshot on or before (score_date - days)."""
    from datetime import date, timedelta

    cutoff = (date.fromisoformat(score_date) - timedelta(days=days)).isoformat()
    row = conn.execute(
        sa.select(sa.func.max(portfolio_history.c.snapshot_date)).where(
            portfolio_history.c.snapshot_date <= cutoff
        )
    ).scalar()

    if row is None:
        return None
    return _nav_from_history(conn, row, nav_usd)


# ---------------------------------------------------------------------------
# Action helpers
# ---------------------------------------------------------------------------


def _is_closing(action: str, target_shares: float) -> bool:
    return action in _CLOSING_ACTIONS or abs(target_shares) < _SHARE_ZERO_THRESHOLD


def _load_approved_non_closing(conn: sa.engine.Connection, score_date: str) -> list[dict]:
    """Return APPROVED non-closing rows from position_approvals for score_date."""
    rows = conn.execute(
        sa.select(position_approvals).where(
            (position_approvals.c.rebalance_date == score_date)
            & (position_approvals.c.status == "APPROVED")
        )
    ).fetchall()

    cols = [c.name for c in position_approvals.columns]
    result = []
    for row in rows:
        d = dict(zip(cols, row, strict=False))
        action = str(d.get("action", "") or "")
        target_shares = float(d.get("target_shares", 0.0) or 0.0)
        if not _is_closing(action, target_shares):
            result.append(d)
    return result


def _apply_close_all(conn: sa.engine.Connection, score_date: str, whatif: bool) -> int:
    """Reject all APPROVED non-closing pending trades. Returns count modified."""
    rows = _load_approved_non_closing(conn, score_date)
    if not rows or whatif:
        return len(rows)

    now = datetime.now(UTC).isoformat(timespec="seconds")
    ids = [r["id"] for r in rows]
    conn.execute(
        position_approvals.update()
        .where(position_approvals.c.id.in_(ids))
        .values(status="REJECTED", reviewed_at=now)
    )
    conn.commit()
    logger.info("circuit_breakers: close_all rejected %d trades", len(ids))
    return len(ids)


def _apply_size_down(
    conn: sa.engine.Connection, score_date: str, factor: float, whatif: bool
) -> int:
    """Scale target_shares by factor for all APPROVED non-closing pending trades.

    Recomputes delta_shares = new_target - current_shares.
    Returns count modified.
    """
    rows = _load_approved_non_closing(conn, score_date)
    if not rows or whatif:
        return len(rows)

    now = datetime.now(UTC).isoformat(timespec="seconds")
    count = 0
    for row in rows:
        old_target = float(row.get("target_shares", 0.0) or 0.0)
        current = float(row.get("current_shares", 0.0) or 0.0)
        new_target = round(old_target * factor)
        new_delta = new_target - current
        conn.execute(
            position_approvals.update()
            .where(position_approvals.c.id == row["id"])
            .values(
                target_shares=new_target,
                delta_shares=new_delta,
                reviewed_at=now,
            )
        )
        count += 1

    conn.commit()
    logger.info("circuit_breakers: size_down (factor=%.2f) modified %d trades", factor, count)
    return count


def _apply_force_close(
    conn: sa.engine.Connection,
    score_date: str,
    ticker: str,
    direction: str,
) -> None:
    """Reject any APPROVED BUY/SHORT trade for ticker; insert a target_shares=0 row."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    open_actions = {"BUY", "SHORT"}

    # Reject existing APPROVED opening trades for this ticker
    rows = conn.execute(
        sa.select(position_approvals).where(
            (position_approvals.c.rebalance_date == score_date)
            & (position_approvals.c.ticker == ticker)
            & (position_approvals.c.status == "APPROVED")
        )
    ).fetchall()

    cols = [c.name for c in position_approvals.columns]
    for row in rows:
        d = dict(zip(cols, row, strict=False))
        action = str(d.get("action", "") or "")
        if action in open_actions:
            conn.execute(
                position_approvals.update()
                .where(position_approvals.c.id == d["id"])
                .values(status="REJECTED", reviewed_at=now)
            )

    # Insert a closing trade targeting zero shares
    close_action = "SELL" if direction == "LONG" else "COVER"

    # Determine current_shares from portfolio_positions
    pos_row = conn.execute(
        sa.select(portfolio_positions.c.shares).where(portfolio_positions.c.ticker == ticker)
    ).fetchone()
    current_shares = float(pos_row[0]) if pos_row and pos_row[0] is not None else 0.0

    conn.execute(
        position_approvals.insert().values(
            rebalance_date=score_date,
            ticker=ticker,
            action=close_action,
            target_shares=0.0,
            current_shares=current_shares,
            delta_shares=-current_shares,
            estimated_cost_usd=None,
            status="APPROVED",
            created_at=now,
            reviewed_at=now,
        )
    )
    conn.commit()
    logger.info(
        "circuit_breakers: force_close inserted %s trade for %s (current_shares=%.0f)",
        close_action,
        ticker,
        current_shares,
    )


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------


def _log_event(
    conn: sa.engine.Connection,
    event_date: str,
    event_type: str,
    trigger: str,
    detail_dict: dict,
) -> None:
    """Insert one row into risk_events."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    conn.execute(
        risk_events.insert().values(
            event_date=event_date,
            event_type=event_type,
            trigger=trigger,
            detail=json.dumps(detail_dict),
            recorded_at=now,
        )
    )
    conn.commit()


def _log_check(
    conn: sa.engine.Connection,
    run_date: str,
    check_type: str,
    ticker: str | None,
    result: str,
    reason: str,
) -> None:
    """Insert one row into risk_log."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    conn.execute(
        risk_log.insert().values(
            run_date=run_date,
            check_type=check_type,
            ticker=ticker,
            result=result,
            reason=reason,
            recorded_at=now,
        )
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------


def _load_config(config: dict) -> dict:
    cb = config.get("risk", {}).get("circuit_breakers", {})
    return {
        "drawdown_kill": float(cb.get("drawdown_kill", 0.08)),
        "daily_close_all": float(cb.get("daily_close_all", 0.025)),
        "daily_size_down": float(cb.get("daily_size_down", 0.015)),
        "weekly_size_down": float(cb.get("weekly_size_down", 0.040)),
        "max_single_position_pct": float(cb.get("max_single_position_pct", 0.03)),
    }


# ---------------------------------------------------------------------------
# Position loader
# ---------------------------------------------------------------------------


def _load_positions(conn: sa.engine.Connection) -> pd.DataFrame:
    rows = conn.execute(sa.select(portfolio_positions)).fetchall()
    cols = [c.name for c in portfolio_positions.columns]
    return pd.DataFrame(rows, columns=cols)
