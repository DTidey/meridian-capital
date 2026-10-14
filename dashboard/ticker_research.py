"""Ticker page: AI analysis write-ups, SEC filings and the latest earnings release."""

from __future__ import annotations

import json
import re

import pandas as pd
import sqlalchemy as sa
import streamlit as st

from analysis.db import analysis_results
from dashboard.theme import section_header
from data.db import earnings_transcripts, sec_filings

ANALYZERS = ("earnings", "filing", "insider", "risk")
_FILING_FORMS = ("10-K", "10-Q", "8-K")
_MD_SPECIAL = re.compile(r"([\\`*_\[\]#<>|$~])")
_SEC_PREFIX = "https://www.sec.gov/"


def md_escape(text) -> str:
    """Render model or filing text literally: no markdown, HTML, links or LaTeX ($)."""
    return "" if text is None else _MD_SPECIAL.sub(r"\\\1", str(text))


def parse_result(raw: str | None) -> dict | None:
    try:
        result = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        return None
    return result if isinstance(result, dict) else None


def analysis_markdown(result: dict) -> str:
    """Generic rendering of an analyzer's result JSON; every value is escaped."""
    parts = []
    for key, value in result.items():
        if key == "one_line_summary":
            continue
        label = md_escape(key.replace("_", " ").capitalize())
        if isinstance(value, dict):
            score = f" ({value['score']:.0f})" if _is_number(value.get("score")) else ""
            parts.append(f"**{label}**{score}: {md_escape(value.get('reasoning', ''))}")
        elif isinstance(value, list):
            bullets = [
                " — ".join(md_escape(v) for v in item.values())
                if isinstance(item, dict)
                else md_escape(item)
                for item in value
            ]
            parts.append(f"**{label}**\n" + "\n".join(f"- {b}" for b in bullets))
        else:
            parts.append(f"**{label}:** {md_escape(value)}")
    return "\n\n".join(parts)


def _is_number(value) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def safe_sec_url(url: str | None) -> str | None:
    return url if url and url.startswith(_SEC_PREFIX) else None


def release_title(row: dict) -> str:
    period = " ".join(str(v) for v in (row.get("quarter"), row.get("year")) if v)
    suffix = f" - {period}" if period else ""
    return f"Earnings release{suffix} ({row.get('earnings_date')})"


def render_ai_analysis(engine, ticker: str) -> None:
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(analysis_results)
            .where(analysis_results.c.ticker == ticker)
            .order_by(analysis_results.c.created_at.desc())
        ).fetchall()
    latest: dict[str, dict] = {}
    for r in rows:
        latest.setdefault(r.analyzer, r._asdict())

    for analyzer in ANALYZERS:
        row = latest.get(analyzer)
        if row is None:
            continue
        result = parse_result(row["result_json"])
        summary = md_escape((result or {}).get("one_line_summary") or "")
        label = f"{analyzer.capitalize()} analysis" + (f" — {summary}" if summary else "")
        with st.expander(label):
            if result is None:
                st.caption("Stored analysis could not be read.")
                continue
            st.markdown(analysis_markdown(result))
            st.caption(f"{row['model']} · {row['created_at'][:10]}")


def render_filings(engine, ticker: str) -> None:
    section_header("SEC FILINGS")
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(sec_filings.c.filed_date, sec_filings.c.form_type, sec_filings.c.filing_url)
            .where((sec_filings.c.ticker == ticker) & sec_filings.c.form_type.in_(_FILING_FORMS))
            .order_by(sec_filings.c.filed_date.desc())
            .limit(15)
        ).fetchall()
    if not rows:
        st.caption("No 10-K, 10-Q or 8-K filings loaded.")
    else:
        df = pd.DataFrame(
            [(r.filed_date, r.form_type, safe_sec_url(r.filing_url)) for r in rows],
            columns=["Filed", "Form", "Link"],
        )
        st.dataframe(
            df,
            width="stretch",
            hide_index=True,
            column_config={"Link": st.column_config.LinkColumn(display_text="EDGAR")},
        )
    _render_earnings_release(engine, ticker)


def _render_earnings_release(engine, ticker: str) -> None:
    with engine.connect() as conn:
        row = conn.execute(
            sa.select(earnings_transcripts)
            .where(earnings_transcripts.c.ticker == ticker)
            .order_by(earnings_transcripts.c.earnings_date.desc())
            .limit(1)
        ).fetchone()
    if row is None:
        return
    row = row._asdict()
    with st.expander(release_title(row)):
        st.text_area(
            "Release text",
            row["content"] or "",
            height=320,
            disabled=True,
            label_visibility="collapsed",
        )
