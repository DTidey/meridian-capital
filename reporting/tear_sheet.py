"""Institutional-format markdown tear sheet — no external plot libraries needed."""

from __future__ import annotations

import logging
import math
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import sqlalchemy as sa

from execution.costs import slippage_stats
from factors.db import factor_scores as factor_scores_table
from portfolio.db import portfolio_positions
from reporting.db import pnl_attribution, portfolio_nav
from reporting.turnover import compute as compute_turnover

if TYPE_CHECKING:
    import sqlalchemy.engine

log = logging.getLogger(__name__)

_BAR_CHARS = "▁▂▃▄▅▆▇█"
_SPARKLEN = 60


def write(
    engine: sqlalchemy.engine.Engine,
    cfg: dict | None = None,
    output_path: str = "output/tear_sheet.md",
    inception_date: str = "2024-01-02",
) -> None:
    """Generate and write institutional markdown tear sheet."""
    with engine.connect() as conn:
        nav_rows = conn.execute(sa.select(portfolio_nav).order_by(portfolio_nav.c.date)).fetchall()
        _attr_rows = conn.execute(
            sa.select(pnl_attribution).order_by(pnl_attribution.c.date)
        ).fetchall()
        pos_rows = conn.execute(
            sa.select(
                portfolio_positions.c.sector,
                portfolio_positions.c.direction,
                portfolio_positions.c.weight,
            )
        ).fetchall()
        factor_row = conn.execute(
            sa.select(factor_scores_table)
            .order_by(factor_scores_table.c.score_date.desc())
            .limit(1)
        ).fetchone()
        slip = slippage_stats(conn, days=30)

    if not nav_rows:
        log.warning("No NAV data — tear sheet will be sparse")
        return

    nav_df = pd.DataFrame(nav_rows, columns=portfolio_nav.columns.keys())
    nav_df["date"] = pd.to_datetime(nav_df["date"])
    nav_df = nav_df.set_index("date").sort_index()

    daily_rets = nav_df["nav"].pct_change().dropna()

    cfg_port = (cfg or {}).get("portfolio", {})
    aum = float(nav_df["nav"].iloc[-1]) if not nav_df.empty else 0.0
    today = date.today().strftime("%d %B %Y")
    _report_date = date.today().isoformat()

    lines: list[str] = []

    # -----------------------------------------------------------------------
    # Header
    # -----------------------------------------------------------------------
    lines += [
        "# MERIDIAN CAPITAL PARTNERS",
        f"**Inception:** {inception_date}  |  **AUM:** ${aum:,.0f}  |  **Report Date:** {today}",
        "",
        "---",
        "",
    ]

    # -----------------------------------------------------------------------
    # Performance vs SPY
    # -----------------------------------------------------------------------
    spy_df = nav_df[["spy_close"]].rename(columns={"spy_close": "spy"})
    spy_rets = spy_df["spy"].pct_change().dropna()
    rf = 0.05

    port_stats = _perf_stats(daily_rets, rf, nav_df)
    spy_stats = _perf_stats(spy_rets, rf, spy_df.rename(columns={"spy": "nav"}) * 1.0)

    lines += [
        "## Performance vs SPY",
        "",
        "| Metric | Portfolio | SPY |",
        "|---|---|---|",
    ]
    for label, key in [
        ("Annualised Return", "ann_return"),
        ("Sharpe Ratio", "sharpe"),
        ("Sortino Ratio", "sortino"),
        ("Max Drawdown", "max_dd"),
        ("Beta", "beta"),
        ("Jensen's Alpha", "alpha"),
        ("Calmar Ratio", "calmar"),
    ]:
        p_val = port_stats.get(key, 0.0)
        s_val = spy_stats.get(key, 0.0)
        fmt = _fmt_stat(key)
        lines.append(f"| {label} | {p_val:{fmt}} | {s_val:{fmt}} |")

    lines += ["", "---", ""]

    # -----------------------------------------------------------------------
    # Monthly Returns Grid
    # -----------------------------------------------------------------------
    lines += ["## Monthly Returns", ""]
    monthly = _monthly_grid(daily_rets)
    if monthly is not None:
        lines += [monthly, ""]

    lines += ["---", ""]

    # -----------------------------------------------------------------------
    # Equity Curve sparkline
    # -----------------------------------------------------------------------
    nav_indexed = nav_df["nav"] / nav_df["nav"].iloc[0] * 100
    spy_indexed = nav_df["spy_close"].dropna()
    if not spy_indexed.empty:
        spy_indexed = spy_indexed / spy_indexed.iloc[0] * 100

    lines += [
        "## Equity Curve (rebased to 100)",
        "",
        "```",
        f"Portfolio:  {_sparkline(nav_indexed.values)}",
        f"SPY:        {_sparkline(spy_indexed.values) if len(spy_indexed) else '—'}",
        "```",
        "",
        "---",
        "",
    ]

    # -----------------------------------------------------------------------
    # Drawdown
    # -----------------------------------------------------------------------
    dd = nav_df["drawdown_pct"]
    peak_date = nav_df["nav"].idxmax().strftime("%Y-%m-%d")
    trough_idx = dd.idxmax()
    trough_date = trough_idx.strftime("%Y-%m-%d")
    max_dd = float(dd.max())
    recovered = bool(nav_df.loc[trough_idx:, "nav"].max() >= nav_df["nav"].max() * 0.999)

    lines += [
        "## Drawdown",
        "",
        f"- **Max drawdown:** {max_dd:.2%}",
        f"- **Peak date:** {peak_date}",
        f"- **Trough date:** {trough_date}",
        f"- **Recovered:** {'Yes' if recovered else 'No'}",
        "",
        "---",
        "",
    ]

    # -----------------------------------------------------------------------
    # Rolling 12-Month Sharpe sparkline
    # -----------------------------------------------------------------------
    rolling_sharpe = _rolling_sharpe(daily_rets, window=252)
    lines += [
        "## Rolling 12-Month Sharpe",
        "",
        "```",
        f"Sharpe:  {_sparkline(rolling_sharpe.values)}",
        "```",
        "",
        "---",
        "",
    ]

    # -----------------------------------------------------------------------
    # Factor Exposures
    # -----------------------------------------------------------------------
    _FACTOR_SCORE_COLS = [
        "momentum_score",
        "value_score",
        "quality_score",
        "growth_score",
        "revisions_score",
        "insider_score",
        "short_interest_score",
        "institutional_score",
    ]
    if factor_row:
        factor_dict = dict(factor_row._mapping)
        lines += [
            "## Factor Exposures (latest z-scores)",
            "",
            "| Factor | Score (0–100) |",
            "|---|---|",
        ]
        for col in _FACTOR_SCORE_COLS:
            val = factor_dict.get(col)
            label = col.replace("_score", "").replace("_", " ").title()
            lines.append(f"| {label} | {val:.1f} |" if val is not None else f"| {label} | — |")
        lines += ["", "---", ""]

    # -----------------------------------------------------------------------
    # Sector Exposures
    # -----------------------------------------------------------------------
    if pos_rows:
        pos_df = pd.DataFrame(pos_rows, columns=["sector", "direction", "weight"])
        sectors = pos_df["sector"].unique()
        lines += [
            "## Sector Exposures",
            "",
            "| Sector | Long Wt | Short Wt | Net Wt |",
            "|---|---|---|---|",
        ]
        for s in sorted(sectors):
            sub = pos_df[pos_df["sector"] == s]
            lw = sub[sub["direction"] == "LONG"]["weight"].sum()
            sw = sub[sub["direction"] == "SHORT"]["weight"].sum()
            net = lw - sw
            lines.append(f"| {s} | {lw:.2%} | {sw:.2%} | {net:+.2%} |")
        lines += ["", "---", ""]

    # -----------------------------------------------------------------------
    # Turnover
    # -----------------------------------------------------------------------
    tv = compute_turnover(engine, turnover_budget_pct=cfg_port.get("turnover_budget_pct", 0.30))
    lines += [
        "## Turnover",
        "",
        "| Period | Turnover | Budget |",
        "|---|---|---|",
        f"| 30-day | {tv['turnover_30d_pct']:.1%} | {tv['budget_pct']:.1%} |",
        f"| 90-day | {tv['turnover_90d_pct']:.1%} | — |",
        f"| Annualised | {tv['turnover_annualized']:.1%} | — |",
        "",
        f"- **Estimated tax liability:** ${tv['tax_estimate_usd']:,.0f}  "
        f"(ST gains ${tv['short_term_gains']:,.0f} @ 37%, LT gains ${tv['long_term_gains']:,.0f} @ 20%)",
        "",
        "---",
        "",
    ]

    # -----------------------------------------------------------------------
    # Execution
    # -----------------------------------------------------------------------
    lines += [
        "## Recent Execution (30d)",
        "",
        f"- **Mean slippage:** {slip.get('mean_bps', 0):.1f} bps",
        f"- **P95 slippage:** {slip.get('p95_bps', 0):.1f} bps",
        f"- **Filled orders:** {slip.get('count', 0)}",
        "",
    ]

    output = "\n".join(lines)
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(output)
    log.info("Tear sheet written to %s", output_path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sharpe(rets: pd.Series, rf: float = 0.05) -> float:
    if rets.empty or rets.std() == 0:
        return 0.0
    ann_ret = float(rets.mean() * 252)
    ann_vol = float(rets.std() * math.sqrt(252))
    return (ann_ret - rf) / ann_vol


def _sortino(rets: pd.Series, rf: float = 0.05) -> float:
    downside = rets[rets < 0]
    if downside.empty or downside.std() == 0:
        return 0.0
    ann_ret = float(rets.mean() * 252)
    downside_vol = float(downside.std() * math.sqrt(252))
    return (ann_ret - rf) / downside_vol


def _calmar(rets: pd.Series, max_dd: float) -> float:
    if max_dd <= 0:
        return 0.0
    return float(rets.mean() * 252) / max_dd


def _perf_stats(rets: pd.Series, rf: float, nav_df: pd.DataFrame) -> dict:
    if rets.empty:
        return {}
    n = len(rets)
    ann_ret = float((1 + rets).prod() ** (252 / max(n, 1)) - 1)
    _ann_vol = float(rets.std() * math.sqrt(252))
    sharpe = _sharpe(rets, rf)
    sortino = _sortino(rets, rf)
    nav = nav_df["nav"] if "nav" in nav_df.columns else pd.Series(dtype=float)
    peak = nav.cummax() if not nav.empty else pd.Series([0.0])
    dd = ((peak - nav) / peak).max() if not nav.empty else 0.0
    calmar = _calmar(rets, float(dd))
    return {
        "ann_return": ann_ret,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_dd": float(dd),
        "beta": 0.0,  # computed relative to benchmark only for portfolio
        "alpha": ann_ret - rf,
        "calmar": calmar,
    }


def _fmt_stat(key: str) -> str:
    pct_keys = {"ann_return", "max_dd", "alpha"}
    return ".2%" if key in pct_keys else ".2f"


def _monthly_grid(daily_rets: pd.Series) -> str | None:
    if daily_rets.empty:
        return None
    dr = daily_rets.copy()
    dr.index = pd.to_datetime(dr.index)
    monthly = (1 + dr).resample("ME").prod() - 1

    years = sorted(monthly.index.year.unique())
    months = list(range(1, 13))
    header = "| Year | " + " | ".join(f"{m:02d}" for m in months) + " | Annual |"
    sep = "|---|" + "---|" * 13

    rows_out = [header, sep]
    for yr in years:
        yr_data = monthly[monthly.index.year == yr]
        cols = []
        annual = 1.0
        for m in months:
            val = yr_data[yr_data.index.month == m]
            if val.empty:
                cols.append(" — ")
                continue
            v = float(val.iloc[0])
            annual *= 1 + v
            sign = "**" if v >= 0 else ""
            cols.append(f"{sign}{v:+.1%}{sign}")
        ann_val = annual - 1.0
        sign = "**" if ann_val >= 0 else ""
        rows_out.append(f"| {yr} | " + " | ".join(cols) + f" | {sign}{ann_val:+.1%}{sign} |")

    return "\n".join(rows_out)


def _sparkline(values: np.ndarray) -> str:
    vals = np.array(values, dtype=float)
    vals = vals[~np.isnan(vals)]
    if len(vals) < 2:
        return "—"
    mn, mx = vals.min(), vals.max()
    if mx == mn:
        return _BAR_CHARS[3] * min(len(vals), _SPARKLEN)
    step = (mx - mn) / (len(_BAR_CHARS) - 1)
    sampled = np.interp(
        np.linspace(0, len(vals) - 1, _SPARKLEN),
        np.arange(len(vals)),
        vals,
    )
    return "".join(_BAR_CHARS[min(int((v - mn) / step), 7)] for v in sampled)


def _rolling_sharpe(rets: pd.Series, window: int = 252, rf: float = 0.05) -> pd.Series:
    if len(rets) < window:
        return pd.Series(dtype=float)
    rolling_mean = rets.rolling(window).mean() * 252
    rolling_std = rets.rolling(window).std() * math.sqrt(252)
    return ((rolling_mean - rf) / rolling_std).dropna()
