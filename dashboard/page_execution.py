"""Page V — Execution: order KPIs, open orders, recent trades, slippage."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import sqlalchemy as sa
import streamlit as st

from dashboard.theme import (
    ACCENT,
    NEUTRAL,
    SHORT_COL,
    metric_card,
    section_header,
)
from execution.costs import slippage_stats
from execution.db import execution_orders
from portfolio.db import portfolio_positions


def render(engine, cfg: dict) -> None:
    cutoff_30 = (date.today() - timedelta(days=30)).isoformat()

    with engine.connect() as conn:
        # KPI data
        filled_30d = (
            conn.execute(
                sa.select(sa.func.count())
                .select_from(execution_orders)
                .where(
                    execution_orders.c.status == "FILLED",
                    execution_orders.c.created_at >= cutoff_30,
                )
            ).scalar()
            or 0
        )

        open_count = (
            conn.execute(
                sa.select(sa.func.count())
                .select_from(execution_orders)
                .where(execution_orders.c.status.in_(["PENDING", "PARTIAL"]))
            ).scalar()
            or 0
        )

        slip = slippage_stats(conn, days=30)

        # Total slippage $ for filled last 30d
        slip_rows = conn.execute(
            sa.select(
                execution_orders.c.slippage_bps,
                execution_orders.c.filled_shares,
                execution_orders.c.avg_fill_price,
            ).where(
                execution_orders.c.status == "FILLED",
                execution_orders.c.created_at >= cutoff_30,
                execution_orders.c.slippage_bps.isnot(None),
            )
        ).fetchall()
        total_slip_usd = sum(
            (r[0] / 10_000) * (r[1] or 0) * (r[2] or 0) for r in slip_rows if r[0] and r[1] and r[2]
        )

        # Recent trades (last 200)
        recent_rows = conn.execute(
            sa.select(execution_orders).order_by(execution_orders.c.created_at.desc()).limit(200)
        ).fetchall()

        # Worst 5 fills (highest slippage_bps)
        worst_rows = conn.execute(
            sa.select(execution_orders)
            .where(
                execution_orders.c.status == "FILLED",
                execution_orders.c.slippage_bps.isnot(None),
            )
            .order_by(execution_orders.c.slippage_bps.desc())
            .limit(5)
        ).fetchall()

        # Open orders from DB
        open_rows = conn.execute(
            sa.select(execution_orders)
            .where(execution_orders.c.status.in_(["PENDING", "PARTIAL"]))
            .order_by(execution_orders.c.created_at.desc())
        ).fetchall()

        # Short positions
        short_tickers = [
            r[0]
            for r in conn.execute(
                sa.select(portfolio_positions.c.ticker).where(
                    portfolio_positions.c.direction == "SHORT"
                )
            ).fetchall()
        ]

        # Daily notional turnover
        notional_rows = conn.execute(
            sa.select(
                execution_orders.c.rebalance_date,
                sa.func.sum(
                    execution_orders.c.filled_shares * execution_orders.c.avg_fill_price
                ).label("notional"),
            )
            .where(
                execution_orders.c.status == "FILLED",
                execution_orders.c.filled_shares.isnot(None),
                execution_orders.c.avg_fill_price.isnot(None),
            )
            .group_by(execution_orders.c.rebalance_date)
            .order_by(execution_orders.c.rebalance_date.desc())
            .limit(30)
        ).fetchall()

    # -----------------------------------------------------------------------
    # Section 1 — KPI Row
    # -----------------------------------------------------------------------
    section_header("EXECUTION SUMMARY")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        metric_card("Filled Orders 30d", str(filled_30d))
    with c2:
        metric_card("Avg Slippage (bps)", f"{slip.get('mean_bps', 0):.1f}")
    with c3:
        metric_card("Total Slippage $", f"${total_slip_usd:,.0f}", SHORT_COL)
    with c4:
        metric_card("Open Orders", str(open_count), "#f59e0b" if open_count else NEUTRAL)

    # -----------------------------------------------------------------------
    # Section 2 — Open Orders Table
    # -----------------------------------------------------------------------
    section_header("OPEN ORDERS")
    col_r, _ = st.columns([1, 4])
    with col_r:
        _refresh = st.button("↺ Refresh")

    if open_rows:
        open_df = pd.DataFrame(open_rows, columns=execution_orders.columns.keys())
        st.dataframe(
            open_df[
                [
                    "rebalance_date",
                    "ticker",
                    "action",
                    "ordered_shares",
                    "filled_shares",
                    "status",
                    "created_at",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        # Try live Alpaca query
        try:
            from alpaca.trading.enums import QueryOrderStatus
            from alpaca.trading.requests import GetOrdersRequest

            from execution.broker import get_client

            client = get_client()
            req = GetOrdersRequest(status=QueryOrderStatus.OPEN)
            orders = client.get_orders(filter=req)
            if orders:
                rows_live = [
                    {
                        "id": str(o.id),
                        "symbol": o.symbol,
                        "side": str(o.side),
                        "qty": o.qty,
                        "filled_qty": o.filled_qty,
                        "status": str(o.status),
                        "submitted_at": str(o.submitted_at),
                    }
                    for o in orders
                ]
                st.dataframe(pd.DataFrame(rows_live), use_container_width=True, hide_index=True)
            else:
                st.caption("No open orders.")
        except Exception as e:
            st.caption(f"No open orders. (Alpaca unavailable: {e})")

    # -----------------------------------------------------------------------
    # Section 3 — Recent Trades Log
    # -----------------------------------------------------------------------
    section_header("RECENT TRADES (last 200)")
    if recent_rows:
        rec_df = pd.DataFrame(recent_rows, columns=execution_orders.columns.keys())
        disp_cols = [
            "rebalance_date",
            "ticker",
            "action",
            "ordered_shares",
            "filled_shares",
            "avg_fill_price",
            "slippage_bps",
            "status",
        ]
        rec_disp = rec_df[disp_cols].copy()

        def _row_colour(row):
            if row["status"] in ("FAILED", "CANCELLED"):
                return [f"color: {SHORT_COL}"] * len(row)
            return [""] * len(row)

        styled = rec_disp.style.apply(_row_colour, axis=1).format(
            {
                "avg_fill_price": "{:.2f}",
                "slippage_bps": "{:.1f}",
                "ordered_shares": "{:.0f}",
                "filled_shares": "{:.0f}",
            },
            na_rep="—",
        )
        st.dataframe(styled, use_container_width=True, hide_index=True, height=350)
    else:
        st.caption("No execution history yet.")

    # -----------------------------------------------------------------------
    # Section 4 — Worst 5 Fills
    # -----------------------------------------------------------------------
    section_header("WORST 5 FILLS (by slippage)")
    if worst_rows:
        w_df = pd.DataFrame(worst_rows, columns=execution_orders.columns.keys())
        st.dataframe(
            w_df[
                [
                    "rebalance_date",
                    "ticker",
                    "action",
                    "filled_shares",
                    "avg_fill_price",
                    "slippage_bps",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("No filled orders with slippage data.")

    # -----------------------------------------------------------------------
    # Section 5 — Short Availability
    # -----------------------------------------------------------------------
    section_header("SHORT AVAILABILITY")
    if short_tickers:
        from execution.short_check import is_shortable

        avail_data = []
        for t in short_tickers:
            try:
                shortable = is_shortable(t)
            except Exception:
                shortable = None
            avail_data.append(
                {
                    "Ticker": t,
                    "Shortable": "✓" if shortable else ("✗" if shortable is False else "?"),
                }
            )
        st.dataframe(pd.DataFrame(avail_data), use_container_width=True, hide_index=True)
    else:
        st.caption("No short positions currently held.")

    # -----------------------------------------------------------------------
    # Section 6 — Daily Notional Turnover
    # -----------------------------------------------------------------------
    section_header("DAILY NOTIONAL TURNOVER (last 30 rebalances)")
    if notional_rows:
        not_df = pd.DataFrame(notional_rows, columns=["date", "notional"])
        not_df = not_df.sort_values("date")
        fig = go.Figure(
            go.Bar(
                x=not_df["date"],
                y=not_df["notional"],
                marker_color=ACCENT,
            )
        )
        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font_color="#e2e8f0",
            height=220,
            margin={"t": 10, "b": 30, "l": 60, "r": 20},
            yaxis={"gridcolor": "rgba(255,255,255,0.05)", "title": "Notional ($)"},
            xaxis={"gridcolor": "rgba(255,255,255,0.05)"},
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.caption("No filled execution data for turnover chart.")
