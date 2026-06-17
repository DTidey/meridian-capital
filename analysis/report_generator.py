"""Markdown report writer for LONG/SHORT candidates."""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa

from analysis.db import combined_scores as combined_scores_table
from data.db import sp500_universe, earnings_calendar

logger = logging.getLogger(__name__)

_SUB_FACTOR_LABELS = {
    "momentum_score":      "Momentum",
    "quality_score":       "Quality",
    "value_score":         "Value",
    "revisions_score":     "Revisions",
    "insider_score":       "Insider",
    "growth_score":        "Growth",
    "short_interest_score": "Short Interest",
    "institutional_score": "Institutional",
}

_EARNINGS_CATEGORIES = [
    ("management_confidence", "Management Confidence"),
    ("revenue_guidance",      "Revenue Guidance"),
    ("margin_trajectory",     "Margin Trajectory"),
    ("competitive_position",  "Competitive Position"),
    ("risk_factors",          "Risk Factors"),
    ("capital_allocation",    "Capital Allocation"),
]


def generate_reports(
    conn: sa.engine.Connection,
    score_date: str,
    analyzer_results: dict[str, dict],
    factor_scores: dict[str, dict],
    sector_results: dict[str, dict],
    config: dict,
    output_dir: str | None = None,
) -> list[str]:
    """Write one markdown file per LONG/SHORT candidate.

    Returns list of file paths written.
    """
    if output_dir is None:
        base = config.get("analysis", {}).get("output_dir", "output/reports")
        output_dir = f"{base}_{score_date.replace('-', '')}"

    Path(output_dir).mkdir(parents=True, exist_ok=True)

    candidates = _load_candidates(conn, score_date)
    if not candidates:
        logger.warning("Report: no LONG/SHORT candidates for %s", score_date)
        return []

    universe_info = _load_universe_info(conn)
    catalysts     = _load_upcoming_catalysts(conn, score_date)

    written = []
    for row in candidates:
        ticker    = row["ticker"]
        direction = row["direction"]
        if direction not in ("LONG", "SHORT"):
            continue

        ai       = analyzer_results.get(ticker, {})
        factors  = factor_scores.get(ticker, {})
        company  = universe_info.get(ticker, {})
        sector   = company.get("sector") or row.get("sector", "Unknown")
        cat_info = catalysts.get(ticker)
        sec_info = sector_results.get(sector)

        md = _build_report(
            ticker=ticker,
            company_name=company.get("name", ticker),
            direction=direction,
            combined_score=row["combined_score"],
            sector=sector,
            score_date=score_date,
            factors=factors,
            ai=ai,
            catalyst=cat_info,
            sector_info=sec_info,
        )

        fname = os.path.join(output_dir, f"{ticker}_{direction.lower()}.md")
        with open(fname, "w") as f:
            f.write(md)
        written.append(fname)
        logger.debug("Report: wrote %s", fname)

    logger.info("Report: wrote %d reports to %s", len(written), output_dir)
    return written


def _load_candidates(conn, score_date: str) -> list[dict]:
    rows = conn.execute(
        sa.select(
            combined_scores_table.c.ticker,
            combined_scores_table.c.combined_score,
            combined_scores_table.c.direction,
            combined_scores_table.c.quant_composite,
            combined_scores_table.c.ai_composite,
        ).where(
            (combined_scores_table.c.score_date == score_date) &
            (combined_scores_table.c.direction  != "NEUTRAL")
        ).order_by(combined_scores_table.c.combined_score.desc())
    ).fetchall()
    return [
        {
            "ticker":          r[0],
            "combined_score":  r[1],
            "direction":       r[2],
            "quant_composite": r[3],
            "ai_composite":    r[4],
        }
        for r in rows
    ]


def _load_universe_info(conn) -> dict[str, dict]:
    rows = conn.execute(
        sa.select(
            sp500_universe.c.ticker,
            sp500_universe.c.company_name,
            sp500_universe.c.gics_sector,
        )
    ).fetchall()
    return {r[0]: {"name": r[1], "sector": r[2]} for r in rows}


def _load_upcoming_catalysts(conn, score_date: str) -> dict[str, dict]:
    rows = conn.execute(
        sa.select(
            earnings_calendar.c.ticker,
            earnings_calendar.c.earnings_date,
            earnings_calendar.c.eps_estimate,
        ).where(earnings_calendar.c.earnings_date > score_date)
        .order_by(earnings_calendar.c.earnings_date.asc())
    ).fetchall()

    result: dict[str, dict] = {}
    for r in rows:
        if r[0] not in result:
            result[r[0]] = {"earnings_date": r[1], "eps_estimate": r[2]}
    return result


def _build_report(
    ticker: str,
    company_name: str,
    direction: str,
    combined_score: float,
    sector: str,
    score_date: str,
    factors: dict,
    ai: dict,
    catalyst: dict | None,
    sector_info: dict | None,
) -> str:
    lines = [
        f"# {ticker} — {company_name}",
        f"**Direction:** {direction} | **Score:** {combined_score:.1f}/100 | **Sector:** {sector}",
        f"**Score date:** {score_date}",
        "",
        "---",
        "",
    ]

    # Quantitative scores
    lines += ["## Quantitative Scores", ""]
    lines += ["| Factor | Score |", "|--------|-------|"]
    if factors.get("composite_score") is not None:
        lines.append(f"| Composite | {factors['composite_score']:.1f} |")
    for col, label in _SUB_FACTOR_LABELS.items():
        val = factors.get(col)
        if val is not None:
            lines.append(f"| {label} | {val:.1f} |")
    lines.append("")

    # AI analysis
    if any(ai.get(k) for k in ("earnings", "filing", "risk", "insider")):
        lines += ["## AI Analysis", ""]

    if ai.get("earnings"):
        lines += _earnings_section(ai["earnings"])

    if ai.get("filing"):
        lines += _filing_section(ai["filing"])

    if ai.get("risk"):
        lines += _risk_section(ai["risk"])

    if ai.get("insider"):
        lines += _insider_section(ai["insider"])

    # Upcoming catalysts
    if catalyst:
        lines += ["## Upcoming Catalysts", ""]
        eps = f" (EPS est. ${catalyst['eps_estimate']:.2f})" if catalyst.get("eps_estimate") else ""
        lines.append(f"- Earnings: {catalyst['earnings_date']}{eps}")
        lines.append("")

    # Sector context
    if sector_info:
        lines += _sector_section(sector, sector_info)

    return "\n".join(lines)


def _earnings_section(result: dict) -> list[str]:
    lines = ["### Earnings Call", ""]
    lines += ["| Category | Score | Summary |", "|----------|-------|---------|"]
    for key, label in _EARNINGS_CATEGORIES:
        cat = result.get(key, {})
        if isinstance(cat, dict):
            score    = cat.get("score", "N/A")
            reasoning = (cat.get("reasoning") or "")[:120]
            lines.append(f"| {label} | {score} | {reasoning} |")
    lines.append("")

    if result.get("bull_case"):
        lines.append(f"**Bull case:** {result['bull_case']}")
    if result.get("bear_case"):
        lines.append(f"**Bear case:** {result['bear_case']}")
    if result.get("key_quotes"):
        lines.append("")
        lines.append("**Key quotes:**")
        for q in result["key_quotes"][:3]:
            lines.append(f"> {q}")
    lines.append("")
    return lines


def _filing_section(result: dict) -> list[str]:
    lines = ["### Filing Quality", ""]
    eq = result.get("earnings_quality_score")
    bs = result.get("balance_sheet_score")
    if eq is not None:
        lines.append(f"**Earnings quality score:** {eq:.1f}/10")
    if bs is not None:
        lines.append(f"**Balance sheet score:** {bs:.1f}/10")
    lines.append("")

    if result.get("green_flags"):
        lines.append("**Green flags:**")
        for flag in result["green_flags"]:
            lines.append(f"- {flag}")
        lines.append("")

    if result.get("red_flags"):
        lines.append("**Red flags:**")
        for flag in result["red_flags"]:
            lines.append(f"- {flag}")
        lines.append("")

    return lines


def _risk_section(result: dict) -> list[str]:
    lines = ["### Risk Factors (10-K)", ""]
    lines.append(f"**Overall severity:** {result.get('risk_severity', 'N/A')}")
    bp = result.get("boilerplate_percentage")
    if bp is not None:
        lines.append(f"**Boilerplate:** {bp}%")
    lines.append("")

    if result.get("material_risks"):
        lines.append("**Material risks:**")
        for r in result["material_risks"][:5]:
            sev  = r.get("severity", "")
            risk = r.get("risk", "")
            cat  = r.get("category", "")
            lines.append(f"- [{sev}] {risk}" + (f" *(category: {cat})*" if cat else ""))
        lines.append("")

    if result.get("new_risks"):
        lines.append("**New risks vs prior filing:**")
        for r in result["new_risks"][:3]:
            lines.append(f"- {r}")
        lines.append("")

    return lines


def _insider_section(result: dict) -> list[str]:
    lines = ["### Insider Activity (90 days)", ""]
    lines.append(f"**Signal:** {result.get('signal_strength', 'N/A')} "
                 f"(confidence: {result.get('confidence', 'N/A')})")
    lines.append("")

    if result.get("key_transactions"):
        lines.append("**Key transactions:**")
        for txn in result["key_transactions"][:5]:
            lines.append(f"- {txn}")
        lines.append("")

    if result.get("reasoning"):
        lines.append(f"**Reasoning:** {result['reasoning']}")
        lines.append("")

    return lines


def _sector_section(sector: str, info: dict) -> list[str]:
    lines = [f"## Sector Context ({sector})", ""]
    outlook = info.get("sector_outlook", "N/A")
    reasoning = info.get("sector_reasoning", "")
    lines.append(f"**Outlook:** {outlook}")
    if reasoning:
        lines.append(f"> {reasoning}")
    lines.append("")

    rankings = info.get("rankings", [])
    if rankings:
        lines.append("**Sector rankings:**")
        for r in rankings[:10]:
            lines.append(f"{r.get('rank', '?')}. {r.get('ticker', '?')} — {r.get('rationale', '')}")
        lines.append("")

    return lines
