"""Page II — Research: factor heatmap, candidate cards, approval + execute."""

from __future__ import annotations

import subprocess  # nosec B404
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import sqlalchemy as sa
import streamlit as st

from analysis.db import analysis_results
from dashboard.theme import (
    LONG_COL,
    NEUTRAL,
    SHORT_COL,
    metric_card,
    section_header,
)
from data.db import daily_prices, earnings_calendar
from factors.db import factor_scores as factor_scores_table
from portfolio.db import portfolio_positions, position_approvals

_FACTOR_COLS = [
    "momentum_score",
    "value_score",
    "quality_score",
    "growth_score",
    "revisions_score",
    "insider_score",
    "short_interest_score",
    "institutional_score",
]

# Approximate FOMC dates 2025-2026
_FOMC_DATES = [
    "2025-01-29",
    "2025-03-19",
    "2025-05-07",
    "2025-06-18",
    "2025-07-30",
    "2025-09-17",
    "2025-10-29",
    "2025-12-10",
    "2026-01-28",
    "2026-03-18",
    "2026-04-29",
    "2026-06-17",
]


def render(engine, cfg: dict) -> None:
    # -----------------------------------------------------------------------
    # Section 1 — KPIs
    # -----------------------------------------------------------------------
    section_header("RESEARCH OVERVIEW")
    with engine.connect() as conn:
        latest_score_date = conn.execute(
            sa.select(sa.func.max(factor_scores_table.c.score_date))
        ).scalar()
        universe_size = (
            conn.execute(
                sa.select(sa.func.count())
                .select_from(factor_scores_table)
                .where(factor_scores_table.c.score_date == latest_score_date)
            ).scalar()
            or 0
            if latest_score_date
            else 0
        )

        long_cands = (
            conn.execute(
                sa.select(sa.func.count())
                .select_from(factor_scores_table)
                .where(
                    factor_scores_table.c.score_date == latest_score_date,
                    factor_scores_table.c.direction == "LONG",
                )
            ).scalar()
            or 0
            if latest_score_date
            else 0
        )

        short_cands = (
            conn.execute(
                sa.select(sa.func.count())
                .select_from(factor_scores_table)
                .where(
                    factor_scores_table.c.score_date == latest_score_date,
                    factor_scores_table.c.direction == "SHORT",
                )
            ).scalar()
            or 0
            if latest_score_date
            else 0
        )

        vix_row = conn.execute(
            sa.select(daily_prices.c.adj_close)
            .where(daily_prices.c.ticker == "^VIX")
            .order_by(daily_prices.c.date.desc())
            .limit(1)
        ).fetchone()
        _vix = float(vix_row[0]) if vix_row else None

        # Regime from factor_scores
        regime_row = conn.execute(
            sa.select(factor_scores_table.c.regime)
            .where(factor_scores_table.c.score_date == latest_score_date)
            .limit(1)
        ).fetchone()
        regime = regime_row[0] if regime_row else "—"

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        metric_card("Universe", str(universe_size))
    with c2:
        metric_card("Long Cands", str(long_cands), LONG_COL)
    with c3:
        metric_card("Short Cands", str(short_cands), SHORT_COL)
    with c4:
        metric_card("Regime", str(regime), NEUTRAL)
    with c5:
        metric_card("Score Date", latest_score_date or "—")

    # -----------------------------------------------------------------------
    # Section 2 — Rebalance Advisory Banner
    # -----------------------------------------------------------------------
    _advisory_banner(engine, cfg)

    # -----------------------------------------------------------------------
    # Section 3 — Optimization toggle (cosmetic only)
    # -----------------------------------------------------------------------
    section_header("PORTFOLIO CONSTRUCTION")
    st.radio(
        "Construction method",
        ["MVO (Mean-Variance)", "Conviction-weighted"],
        horizontal=True,
        key="opt_mode",
        label_visibility="collapsed",
    )

    # -----------------------------------------------------------------------
    # Section 4 — Factor Heatmap
    # -----------------------------------------------------------------------
    section_header("FACTOR HEATMAP — TOP 30 LONGS + BOTTOM 30 SHORTS")
    if latest_score_date:
        _factor_heatmap(engine, latest_score_date)

    # -----------------------------------------------------------------------
    # Section 5 — Approval Banner + Execute
    # -----------------------------------------------------------------------
    _approval_section(engine, cfg)

    # -----------------------------------------------------------------------
    # Section 6 — Candidate Cards
    # -----------------------------------------------------------------------
    section_header("CANDIDATES")
    if latest_score_date:
        _candidate_cards(engine, latest_score_date, cfg)


def _advisory_banner(engine, cfg: dict) -> None:
    today = date.today()
    today_str = today.isoformat()
    blackout = int((cfg.get("portfolio") or {}).get("earnings_blackout_days", 5))

    warnings = []

    with engine.connect() as conn:
        held = [r[0] for r in conn.execute(sa.select(portfolio_positions.c.ticker)).fetchall()]

        upcoming_earn = conn.execute(
            sa.select(earnings_calendar.c.ticker).where(
                earnings_calendar.c.ticker.in_(held),
                earnings_calendar.c.earnings_date >= today_str,
                earnings_calendar.c.earnings_date <= (today + timedelta(days=blackout)).isoformat(),
            )
        ).fetchall()
        if upcoming_earn:
            tickers = [r[0] for r in upcoming_earn]
            warnings.append(f"⚠️ Earnings blackout within {blackout}d: {', '.join(tickers)}")

    # FOMC check
    for fomc in _FOMC_DATES:
        try:
            delta = (date.fromisoformat(fomc) - today).days
            if 0 <= delta <= 3:
                warnings.append(f"⚠️ FOMC meeting in {delta}d ({fomc})")
        except ValueError:
            pass

    # OpEx (third Friday of the month)
    opex = _next_opex()
    delta_opex = (opex - today).days
    if 0 <= delta_opex <= 3:
        warnings.append(f"⚠️ Monthly options expiry (OpEx) in {delta_opex}d ({opex.isoformat()})")

    for w in warnings:
        st.warning(w)


def _next_opex() -> date:
    today = date.today()
    # Find third Friday of current month
    d = date(today.year, today.month, 1)
    fridays = 0
    while True:
        if d.weekday() == 4:
            fridays += 1
            if fridays == 3:
                return d
        d = date.fromordinal(d.toordinal() + 1)


def _factor_heatmap(engine, score_date: str) -> None:
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(
                factor_scores_table.c.ticker,
                factor_scores_table.c.direction,
                factor_scores_table.c.composite_score,
                *[factor_scores_table.c[c] for c in _FACTOR_COLS],
            )
            .where(factor_scores_table.c.score_date == score_date)
            .order_by(factor_scores_table.c.composite_score.desc())
        ).fetchall()

    if not rows:
        st.info("No factor scores available.")
        return

    df = pd.DataFrame(rows, columns=["ticker", "direction", "composite"] + _FACTOR_COLS)
    top30 = df[df["direction"] == "LONG"].head(30)
    bot30 = df[df["direction"] == "SHORT"].tail(30)
    display = pd.concat([top30, bot30])[["ticker", "direction", "composite"] + _FACTOR_COLS]
    display.columns = ["Ticker", "Dir", "Composite"] + [
        c.replace("_score", "").replace("_", " ").title() for c in _FACTOR_COLS
    ]

    def _colour_dir(val):
        if val == "LONG":
            return f"color: {LONG_COL}"
        if val == "SHORT":
            return f"color: {SHORT_COL}"
        return ""

    score_cols = display.columns[2:]
    styled = (
        display.style.map(_colour_dir, subset=["Dir"])
        .background_gradient(subset=list(score_cols), cmap="RdYlGn", vmin=0, vmax=100)
        .format(dict.fromkeys(score_cols, "{:.0f}"))
    )
    st.dataframe(styled, use_container_width=True, height=500)


def _approval_section(engine, cfg: dict) -> None:
    with engine.connect() as conn:
        pending = conn.execute(
            sa.select(position_approvals).where(position_approvals.c.status == "PENDING")
        ).fetchall()

    if not pending:
        return

    section_header(f"PENDING APPROVAL — {len(pending)} TRADE(S)")
    st.warning(f"{len(pending)} trades pending approval.")

    if st.button("⚡ Execute All Approved Trades", type="primary"):
        vetoes = _run_veto_checks(engine, cfg, pending)
        passed = [p for p in pending if p[1] not in vetoes]

        if vetoes:
            for ticker, reason in vetoes.items():
                st.markdown(
                    f'<div class="veto-reason">⛔ <b>{ticker}</b>: {reason}</div>',
                    unsafe_allow_html=True,
                )

        if passed:
            with engine.begin() as conn:
                for row in passed:
                    conn.execute(
                        position_approvals.update()
                        .where(
                            position_approvals.c.id == row[0],
                            position_approvals.c.status == "PENDING",
                        )
                        .values(status="APPROVED")
                    )
            with st.spinner("Submitting orders to Alpaca…"):
                root = Path(__file__).parent.parent
                result = subprocess.run(  # nosec B603
                    [sys.executable, str(root / "run_execution.py")], capture_output=True, text=True
                )
            if result.returncode == 0:
                st.success(f"Submitted {len(passed)} orders.")
            else:
                st.error(f"Execution error: {result.stderr[-500:]}")
        else:
            st.error("All trades vetoed — none submitted.")


def _run_veto_checks(engine, cfg: dict, pending) -> dict[str, str]:
    """Return {ticker: veto_reason} for trades that fail any of the 8 checks."""
    from datetime import date as _date
    from datetime import datetime as _dt
    from pathlib import Path

    import pytz

    vetoes: dict[str, str] = {}

    # Check 1 — Halt lock
    if (Path("cache") / "halt.lock").exists():
        for row in pending:
            vetoes[row[1]] = "Halt lock active"
        return vetoes

    # Check 2 — Market hours
    et = pytz.timezone("America/New_York")
    now = _dt.now(et)
    if now.weekday() >= 5 or not ((9, 30) <= (now.hour, now.minute) <= (16, 0)):
        for row in pending:
            vetoes[row[1]] = "Market closed"
        return vetoes

    today = _date.today().isoformat()
    risk_cfg = cfg.get("risk", {})
    cb_cfg = risk_cfg.get("circuit_breakers", {})

    with engine.connect() as conn:
        from risk.db import risk_events as risk_ev

        # Check 3 — Daily loss circuit breaker
        severe_events = conn.execute(
            sa.select(risk_ev.c.event_type).where(risk_ev.c.event_date == today)
        ).fetchall()
        severe_types = {r[0] for r in severe_events}

        if "CLOSE_ALL" in severe_types or "KILL_SWITCH" in severe_types:
            for row in pending:
                vetoes[row[1]] = "Circuit breaker: CLOSE_ALL / KILL_SWITCH active"
            return vetoes

        # Check 4 — Kill switch (included above)

        # Check 5 — Gross exposure
        from portfolio.db import portfolio_positions as pp

        pos_rows = conn.execute(sa.select(pp.c.weight, pp.c.direction)).fetchall()
        gross = sum(abs(r[0]) for r in pos_rows)
        max_gross = float(cb_cfg.get("max_gross", 1.65))
        if gross >= max_gross:
            for row in pending:
                vetoes[row[1]] = f"Gross exposure {gross:.2%} ≥ limit {max_gross:.2%}"

        # Check 6 — Net exposure
        net = sum(r[0] if r[1] == "LONG" else -r[0] for r in pos_rows)
        pt_cfg = risk_cfg.get("pre_trade", {})
        net_min = float(pt_cfg.get("net_min", -0.10))
        net_max = float(pt_cfg.get("net_max", 0.15))
        if not (net_min <= net <= net_max):
            for row in pending:
                if row[1] not in vetoes:
                    vetoes[row[1]] = (
                        f"Net exposure {net:.2%} out of bounds [{net_min:.2%}, {net_max:.2%}]"
                    )

        # Check 7 — Earnings blackout
        blackout = int((cfg.get("portfolio") or {}).get("earnings_blackout_days", 5))
        from data.db import earnings_calendar as ec

        for row in pending:
            ticker = row[1]
            earn = conn.execute(
                sa.select(ec.c.earnings_date).where(
                    ec.c.ticker == ticker,
                    ec.c.earnings_date >= today,
                    ec.c.earnings_date <= (_date.today() + timedelta(days=blackout)).isoformat(),
                )
            ).fetchone()
            if earn and ticker not in vetoes:
                vetoes[ticker] = f"Earnings blackout: earnings on {earn[0]}"

    # Check 8 — Short availability
    from execution.short_check import is_shortable

    for row in pending:
        ticker = row[1]
        action = row[3]  # action column
        if action in ("SHORT", "COVER") and ticker not in vetoes:
            try:
                if not is_shortable(ticker):
                    vetoes[ticker] = "Short not available (HTB)"
            except Exception:  # nosec B110
                pass

    return vetoes


def _candidate_cards(engine, score_date: str, cfg: dict) -> None:
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(
                factor_scores_table.c.ticker,
                factor_scores_table.c.direction,
                factor_scores_table.c.composite_score,
                factor_scores_table.c.qual_piotroski,
                factor_scores_table.c.qual_altman_z,
            )
            .where(factor_scores_table.c.score_date == score_date)
            .order_by(factor_scores_table.c.composite_score.desc())
        ).fetchall()

        pos_pnl = {
            r[0]: r[1]
            for r in conn.execute(
                sa.select(
                    portfolio_positions.c.ticker,
                    portfolio_positions.c.unrealized_pnl,
                )
            ).fetchall()
        }

        pending_statuses = {
            r[0]: r[1]
            for r in conn.execute(
                sa.select(
                    position_approvals.c.ticker,
                    position_approvals.c.status,
                ).where(position_approvals.c.rebalance_date == score_date)
            ).fetchall()
        }

        analyses = {}
        for r in conn.execute(
            sa.select(
                analysis_results.c.ticker,
                analysis_results.c.result_json,
            ).where(analysis_results.c.analyzer == "combined")
        ).fetchall():
            analyses[r[0]] = r[1]

    longs = [r for r in rows if r[1] == "LONG"][:10]
    shorts = [r for r in rows if r[1] == "SHORT"][-10:]

    col_l, col_r = st.columns(2)

    for col, candidates, label, badge_col in [
        (col_l, longs, "LONGS", LONG_COL),
        (col_r, shorts, "SHORTS", SHORT_COL),
    ]:
        with col:
            st.markdown(
                f'<div class="section-header" style="color:{badge_col};">{label}</div>',
                unsafe_allow_html=True,
            )
            for row in candidates:
                ticker = row[0]
                score = row[2]
                piotr = row[3]
                altman = row[4]
                upnl = pos_pnl.get(ticker)
                status = pending_statuses.get(ticker, "—")

                with st.expander(
                    f"**{ticker}** · Score {score:.0f}  |  Status: {status}",
                    expanded=False,
                ):
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Composite", f"{score:.0f}")
                    c2.metric("Piotroski", f"{piotr:.1f}" if piotr is not None else "—")
                    c3.metric("Altman-Z", f"{altman:.1f}" if altman is not None else "—")
                    if upnl is not None:
                        st.caption(f"Unrealised P&L: ${upnl:,.0f}")

                    if ticker in analyses:
                        import json

                        try:
                            analysis_data = json.loads(analyses[ticker])
                            st.markdown(analysis_data.get("summary") or str(analysis_data)[:600])
                        except Exception:
                            st.text(str(analyses[ticker])[:400])

                    ba1, ba2, ba3 = st.columns(3)
                    with ba1:
                        if st.button("✓ Approve", key=f"approve_{ticker}"):
                            _set_status(engine, score_date, ticker, "APPROVED")
                            st.rerun()
                    with ba2:
                        if st.button("✗ Reject", key=f"reject_{ticker}"):
                            _set_status(engine, score_date, ticker, "REJECTED")
                            st.rerun()
                    with ba3:
                        if st.button("↺ Reset", key=f"reset_{ticker}"):
                            _set_status(engine, score_date, ticker, "PENDING")
                            st.rerun()


def _set_status(engine, rebalance_date: str, ticker: str, status: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            position_approvals.update()
            .where(
                position_approvals.c.rebalance_date == rebalance_date,
                position_approvals.c.ticker == ticker,
            )
            .values(status=status)
        )
