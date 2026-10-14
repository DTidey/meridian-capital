"""Ticker page: institutional holdings from tracked funds' 13F filings."""

from __future__ import annotations

import math

import pandas as pd
import sqlalchemy as sa
import streamlit as st

from dashboard.theme import LONG_COL, NEUTRAL, SHORT_COL, metric_card, section_header
from data.db import institutional_holdings, institutional_summary


def render_institutional(engine, ticker: str, cfg: dict) -> None:
    section_header("INSTITUTIONAL HOLDINGS")
    with engine.connect() as conn:
        summary = conn.execute(
            sa.select(institutional_summary)
            .where(institutional_summary.c.ticker == ticker)
            .order_by(institutional_summary.c.report_date.desc())
            .limit(1)
        ).fetchone()
        if summary is None:
            st.caption("No institutional holdings among tracked funds.")
            return
        summary = summary._asdict()
        as_of = summary["report_date"]
        rows, fund_periods = _load_holdings(conn, ticker, as_of)

    tracked = len(cfg.get("institutional", {}).get("tracked_funds", []))
    st.caption(f"Tracked funds' 13F filings, quarter ending {as_of}")
    c1, c2, c3 = st.columns(3)
    with c1:
        metric_card("Funds Holding", f"{summary['funds_holding'] or 0} / {tracked}")
    with c2:
        text, colour = net_change_display(summary["net_share_change"])
        metric_card("Net Share Change", text, colour)
    with c3:
        metric_card("New Positions", str(summary["new_positions"] or 0))

    table = fund_holdings(rows, fund_periods, as_of)
    if not table:
        st.caption(f"No tracked fund holds this ticker as of {as_of}.")
        return
    st.dataframe(
        pd.DataFrame(table),
        width="stretch",
        hide_index=True,
        column_config={
            "Shares": st.column_config.NumberColumn(format="%,.0f"),
            "Value ($M)": st.column_config.NumberColumn(format="%,.1f"),
        },
    )


def _load_holdings(conn, ticker: str, as_of: str) -> tuple[list[dict], dict[str, list[str]]]:
    """This ticker's rows, and every fund's filed periods (to tell exits from holds)."""
    rows = [
        r._asdict()
        for r in conn.execute(
            sa.select(
                institutional_holdings.c.fund_name,
                institutional_holdings.c.report_date,
                institutional_holdings.c.filed_date,
                institutional_holdings.c.shares_held,
                institutional_holdings.c.market_value,
            ).where(
                (institutional_holdings.c.ticker == ticker)
                & (institutional_holdings.c.report_date <= as_of)
            )
        )
    ]
    fund_periods: dict[str, list[str]] = {}
    for fund, period in conn.execute(
        sa.select(institutional_holdings.c.fund_name, institutional_holdings.c.report_date)
        .where(institutional_holdings.c.report_date <= as_of)
        .distinct()
        .order_by(institutional_holdings.c.report_date)
    ):
        fund_periods.setdefault(fund, []).append(period)
    return rows, fund_periods


def fund_holdings(rows: list[dict], fund_periods: dict[str, list[str]], as_of: str) -> list[dict]:
    """One row per fund whose latest filing <= as_of holds the ticker, largest value first."""
    by_filing = {(r["fund_name"], r["report_date"]): r for r in rows}
    table = []
    for fund, periods in fund_periods.items():
        filed = sorted(p for p in periods if p <= as_of)
        current = by_filing.get((fund, filed[-1])) if filed else None
        if current is None:
            continue
        prior = by_filing.get((fund, filed[-2])) if len(filed) > 1 else None
        if len(filed) < 2:
            change = "—"
        elif prior is None:
            change = "New"
        else:
            diff = (current["shares_held"] or 0) - (prior["shares_held"] or 0)
            change = f"{diff:+,.0f}" if diff else "0"
        value = current["market_value"]
        table.append(
            {
                "Fund": fund,
                "Quarter": filed[-1],
                "Filed": current["filed_date"],
                "Shares": current["shares_held"],
                "Value ($M)": value / 1e6 if value is not None else None,
                "Change": change,
            }
        )
    return sorted(table, key=lambda r: (r["Value ($M)"] is None, -(r["Value ($M)"] or 0)))


def net_change_display(value: float | None) -> tuple[str, str]:
    if value is None or math.isnan(value):
        return "—", NEUTRAL
    if value == 0:
        return "0", NEUTRAL
    colour = LONG_COL if value > 0 else SHORT_COL
    if abs(value) < 10_000:
        return f"{value:+,.0f}", colour
    return f"{value / 1e6:+.2f}M", colour
