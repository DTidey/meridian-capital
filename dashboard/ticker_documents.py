"""Ticker page: read a filing (exact EDGAR conversion) and its AI summary."""

from __future__ import annotations

import json
import re

import sqlalchemy as sa
import streamlit as st

from analysis import doc_summary
from analysis.cache import AnalysisCache
from analysis.cost_tracker import CostTracker
from analysis.db import analysis_results
from analysis.llm import get_api_key
from dashboard.ticker_research import FILING_FORMS, md_escape, release_title
from data import edgar_docs
from data.db import earnings_transcripts, sec_filings

ANALYZER = "doc_summary"
_KEEP_DAYS = 3650  # filings never change; keep summaries past the analysis cache TTL
_CIK = re.compile(r"/Archives/edgar/data/(\d+)/")


class _FetchFailed(Exception):
    pass


@st.cache_data(ttl=86_400, max_entries=16, show_spinner="Fetching the filing from EDGAR…")
def _filing_markdown(filing_url: str | None, accession: str, form_type: str) -> str:
    md = edgar_docs.fetch_filing_markdown(filing_url, accession, form_type)
    if not md:
        raise _FetchFailed(accession)  # exceptions aren't cached, so the next view retries
    return md


def viewer_options(engine, ticker: str) -> list[dict]:
    """Recent 10-K/10-Q/8-K filings, newest first, plus the latest earnings release's 8-K."""
    with engine.connect() as conn:
        filings = conn.execute(
            sa.select(
                sec_filings.c.filed_date,
                sec_filings.c.form_type,
                sec_filings.c.accession_no,
                sec_filings.c.filing_url,
            )
            .where((sec_filings.c.ticker == ticker) & sec_filings.c.form_type.in_(FILING_FORMS))
            .order_by(sec_filings.c.filed_date.desc())
            .limit(15)
        ).fetchall()
        release = conn.execute(
            sa.select(earnings_transcripts)
            .where(earnings_transcripts.c.ticker == ticker)
            .order_by(earnings_transcripts.c.earnings_date.desc())
            .limit(1)
        ).fetchone()
    options = [
        {
            "label": f"{f.filed_date} {f.form_type}",
            "accession": f.accession_no,
            "form_type": f.form_type,
            "filing_url": f.filing_url,
        }
        for f in filings
    ]
    if release is not None and release.source_accession_no:
        cik = next((m.group(1) for f in filings if (m := _CIK.search(f.filing_url or ""))), None)
        acc = release.source_accession_no
        url = (
            f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/"
            if cik
            else None
        )
        options.insert(
            0,
            {
                "label": release_title(release._asdict()),
                "accession": acc,
                "form_type": "8-K",
                "filing_url": url,
                "stored_text": release.content,
            },
        )
    return options


def render_document_viewer(engine, ticker: str, cfg: dict) -> None:
    options = viewer_options(engine, ticker)
    if not options:
        return
    choice = st.selectbox(
        "Read a filing",
        range(len(options)),
        format_func=lambda i: options[i]["label"],
        index=None,
        placeholder="Choose a filing to read",
        key=f"filing_viewer_{ticker}",
    )
    if choice is None:
        return
    option = options[choice]
    summary_tab, document_tab = st.tabs(["AI summary", "Document"])
    with document_tab:
        _render_document(engine, option)
    with summary_tab:
        _render_summary(engine, ticker, cfg, option)


def _source_text(engine, option: dict) -> tuple[str, bool]:
    """(text, exact): the EDGAR conversion, else the stored text."""
    try:
        return _filing_markdown(
            option["filing_url"], option["accession"], option["form_type"]
        ), True
    except _FetchFailed:
        pass
    if "stored_text" in option:
        return option["stored_text"] or "", False
    with engine.connect() as conn:
        stored = conn.execute(
            sa.select(sec_filings.c.content_text).where(
                sec_filings.c.accession_no == option["accession"]
            )
        ).scalar()
    return stored or "", False


def _render_document(engine, option: dict) -> None:
    text, exact = _source_text(engine, option)
    if exact:
        with st.container(height=700):
            st.markdown(text)
        return
    st.caption("Couldn't fetch the original from EDGAR; showing the stored text.")
    st.text_area("Stored text", text, height=500, disabled=True, label_visibility="collapsed")


def _saved_summary(engine, ticker: str, accession: str):
    with engine.connect() as conn:
        return conn.execute(
            sa.select(analysis_results).where(
                (analysis_results.c.analyzer == ANALYZER)
                & (analysis_results.c.ticker == ticker)
                & (analysis_results.c.artifact_id == accession)
            )
        ).fetchone()


def _render_summary(engine, ticker: str, cfg: dict, option: dict) -> None:
    saved = _saved_summary(engine, ticker, option["accession"])
    if saved is not None:
        result = json.loads(saved.result_json)
        st.markdown(doc_summary.clean_summary(result["markdown"]))
        _summary_notes(
            result, f"{saved.model} · {saved.created_at[:10]} · ${saved.cost_usd or 0:.4f}"
        )
        return
    has_key = bool(get_api_key(cfg))
    if not has_key:
        st.caption("Set the LLM API key to generate summaries.")
    if st.button("Generate summary", disabled=not has_key, key=f"summarise_{option['accession']}"):
        _generate_summary(engine, ticker, cfg, option)


def _generate_summary(engine, ticker: str, cfg: dict, option: dict) -> None:
    source, _exact = _source_text(engine, option)
    if not source:
        st.caption("No text available to summarise.")
        return
    text, truncated = doc_summary.truncate_tokens(source, doc_summary.MAX_SOURCE_TOKENS)
    usage_box: list = []
    placeholder = st.empty()
    raw = ""
    try:
        for delta in doc_summary.stream_summary(cfg, option["form_type"], text, usage_box.append):
            raw += delta
            placeholder.markdown(doc_summary.clean_summary(raw))
    except Exception as exc:
        placeholder.empty()
        st.caption(f"Summary failed: {md_escape(exc)}")
        return
    model = doc_summary.summary_model(cfg)
    usage = usage_box[-1] if usage_box else None
    cost = CostTracker().record(usage, model) if usage is not None else 0.0
    result = {
        "markdown": raw,
        "unverified": doc_summary.unverified_figures(raw, text),
        "truncated": truncated,
    }
    with engine.connect() as conn:
        AnalysisCache(conn, ttl_days=_KEEP_DAYS).set(
            ANALYZER, ticker, option["accession"], model, result, usage, cost
        )
    _summary_notes(result, f"{model} · just now · ${cost:.4f}")


def _summary_notes(result: dict, provenance: str) -> None:
    st.caption(md_escape(provenance))
    if result.get("truncated"):
        st.caption("The filing was too long; this summary covers its first part only.")
    if result.get("unverified"):
        st.caption(
            "Figures not found in the filing, check before relying on them: "
            + md_escape(", ".join(result["unverified"]))
        )
