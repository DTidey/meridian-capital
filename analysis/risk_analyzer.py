"""10-K risk factor analyzer."""

import logging
import re

import sqlalchemy as sa

from analysis.api_client import OpenAIClient
from analysis.cache import AnalysisCache
from analysis.llm import DEFAULT_MODEL
from data.db import sec_filings

logger = logging.getLogger(__name__)

_SEVERITY_MAP = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}

_SYSTEM_PROMPT = """\
You are a risk analyst specialising in SEC 10-K filings.
You will receive the Risk Factors section of a 10-K annual report.
Your tasks:
1. Identify material risks that could genuinely impact the investment thesis.
2. Flag boilerplate/generic language that is not company-specific.
3. Estimate the percentage of the text that is boilerplate.
4. If a prior filing accession number is provided, flag risks that appear new.
Classify overall risk severity as LOW, MEDIUM, HIGH, or CRITICAL.
Your response MUST be a valid JSON object with no additional text."""

_RESPONSE_SCHEMA = """\
{
  "material_risks": [
    {"risk": "<string>", "severity": "LOW|MEDIUM|HIGH|CRITICAL", "category": "<string>"},
    ...
  ],
  "new_risks":              ["<risk description>", ...],
  "boilerplate_percentage": <0-100>,
  "risk_severity":          "LOW|MEDIUM|HIGH|CRITICAL",
  "one_line_summary":       "<string>"
}"""

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def analyse(
    conn: sa.engine.Connection,
    ticker: str,
    client: OpenAIClient,
    cache: AnalysisCache,
    config: dict,
    score_date: str,
) -> dict | None:
    """Analyse the most recent 10-K risk factors section for ticker."""
    filing = _fetch_10k(conn, ticker, score_date)
    if filing is None:
        logger.debug("Risk: no 10-K cached for %s", ticker)
        return None

    accession_no = filing["accession_no"]
    artifact_id = AnalysisCache.artifact_id_risk(ticker, accession_no)

    model = (
        config.get("analysis", {})
        .get("analyzer_models", {})
        .get("risk", config.get("analysis", {}).get("openai_model", DEFAULT_MODEL))
    )
    cached = cache.get("risk", ticker, artifact_id, model=model)
    if cached is not None:
        logger.debug("Risk: cache hit for %s (%s)", ticker, accession_no)
        return cached

    text, _ = _risk_text(filing, config.get("analysis", {}).get("filing_risk_max_chars", 80_000))

    prior_note = ""
    prior = _fetch_prior_10k(conn, ticker, accession_no, score_date)
    if prior:
        prior_note = (
            f"\nPrior filing accession: {prior['accession_no']} (filed {prior['filed_date']})"
        )

    user_prompt = (
        f"Company: {ticker}\n"
        f"Filing accession: {accession_no}"
        f"{prior_note}\n\n"
        f"RISK FACTORS SECTION:\n{text}\n\n"
        f"Return your assessment as JSON matching this schema:\n{_RESPONSE_SCHEMA}"
    )

    logger.info("Risk: calling API for %s (model=%s)", ticker, model)
    result = client.chat(_SYSTEM_PROMPT, user_prompt, model=model)
    result = _validate(result)

    cache.set("risk", ticker, artifact_id, model, result, _zero_usage(), _last_cost(client))
    return result


def risk_score(result: dict | None) -> float | None:
    """4-10 score from the mean severity of the material risks (lower risk -> higher score).

    The overall risk_severity label is not used: with the real Item 1A section nearly every
    company is HIGH. No listed risks means the model saw no usable section, so no score.
    """
    if result is None:
        return None
    risks = result.get("material_risks")
    if not isinstance(risks, list):
        return None
    severities = [r.get("severity") for r in risks if isinstance(r, dict)]
    values = [_SEVERITY_MAP[s] for s in severities if isinstance(s, str) and s in _SEVERITY_MAP]
    if not values:
        return None
    return 10.0 - 2.0 * sum(values) / len(values)


def _fetch_10k(conn, ticker: str, score_date: str) -> dict | None:
    row = conn.execute(
        sa.select(
            sec_filings.c.accession_no,
            sec_filings.c.filed_date,
            sec_filings.c.content_text,
            sec_filings.c.filing_url,
        )
        .where(
            (sec_filings.c.ticker == ticker)
            & (sec_filings.c.form_type == "10-K")
            & (sec_filings.c.filed_date <= score_date)
        )
        .order_by(sec_filings.c.filed_date.desc())
        .limit(1)
    ).fetchone()
    if row is None:
        return None
    return {
        "accession_no": row[0],
        "filed_date": row[1],
        "content_text": row[2],
        "filing_url": row[3],
    }


def _fetch_prior_10k(conn, ticker: str, current_accession: str, score_date: str) -> dict | None:
    row = conn.execute(
        sa.select(
            sec_filings.c.accession_no,
            sec_filings.c.filed_date,
        )
        .where(
            (sec_filings.c.ticker == ticker)
            & (sec_filings.c.form_type == "10-K")
            & (sec_filings.c.accession_no != current_accession)
            & (sec_filings.c.filed_date <= score_date)
        )
        .order_by(sec_filings.c.filed_date.desc())
        .limit(1)
    ).fetchone()
    if row is None:
        return None
    return {"accession_no": row[0], "filed_date": row[1]}


_MIN_SECTION_CHARS = 2_000  # shorter means a contents entry was matched, not the section
# "Item 1A", "Item 1.A", "Item 1(a)", optionally after "Part I."
_ITEM = r"^(?:part\s*i\W*)?item\s*"
_ITEM_1A = re.compile(_ITEM + r"1\.?\s*\(?a\b", re.IGNORECASE)
_SECTION_END = re.compile(_ITEM + r"(1\.?\s*\(?[bc]\b|2\b)", re.IGNORECASE)
# Titles of the 10-K items after 1A, for filings whose headings omit "Item 1A" (EIX, MS)
_LATER_ITEM_TITLES = {
    "unresolved staff comments",
    "cybersecurity",
    "properties",
    "legal proceedings",
    "mine safety disclosures",
    "selected financial data",
    "quantitative and qualitative disclosures about market risk",
    "financial statements and supplementary data",
}
_MDA_TITLE = "management's discussion and analysis"
_MD_ESCAPE = re.compile(r"\\([\\`*_\[\]#<>|$~])")


def _heading(block: str) -> str | None:
    """Normalised text of a short heading-like block (plain line or one-row table), else None."""
    text = re.sub(r"\s+", " ", re.sub(r"[|:]|-{2,}", " ", block)).strip()
    return text if text and len(text) < 80 else None


def _title(head: str) -> str:
    """Heading reduced for title comparison: lower case, straight quotes, no end punctuation."""
    return head.lower().replace("’", "'").rstrip(" .")


def _is_item_1a(head: str) -> bool:
    return bool(_ITEM_1A.match(head)) and "risk factor" in head.lower()


def _is_bare_risk_factors(head: str) -> bool:
    return _title(head) == "risk factors"


def _is_item_end(head: str) -> bool:
    return bool(_SECTION_END.match(head))


def _is_later_item(head: str) -> bool:
    title = _title(head)
    return _is_item_end(head) or title in _LATER_ITEM_TITLES or title.startswith(_MDA_TITLE)


def _longest_section(blocks, is_start, is_end, require_end: bool) -> str | None:
    """Longest run of blocks from a heading matching is_start to the next matching is_end."""
    best: tuple[int, int] | None = None
    for i, block in enumerate(blocks):
        head = _heading(block)
        if not head or not is_start(head):
            continue
        end = next(
            (j for j in range(i + 1, len(blocks)) if (h := _heading(blocks[j])) and is_end(h)),
            None,
        )
        if end is None and require_end:
            continue
        end = len(blocks) if end is None else end
        if best is None or sum(map(len, blocks[i:end])) > sum(map(len, blocks[best[0] : best[1]])):
            best = (i, end)
    return "\n\n".join(blocks[best[0] : best[1]]) if best else None


def extract_risk_factors(markdown: str) -> str | None:
    """Item 1A section of a 10-K converted to markdown, from its heading to Item 1B/1C/2.

    Table-of-contents rows sit in long multi-row tables and cross-references are long
    sentences, so only short heading blocks count; the longest candidate section wins.
    Filings whose heading is a bare "Risk Factors" are used only when no Item 1A section
    is found, and only up to the title of a later item (a page header has no such end).
    """
    blocks = markdown.split("\n\n") if markdown else []
    section = _longest_section(blocks, _is_item_1a, _is_item_end, require_end=False)
    if section and len(section) >= _MIN_SECTION_CHARS:
        return section
    bare = _longest_section(blocks, _is_bare_risk_factors, _is_later_item, require_end=True)
    return bare if bare and len(bare) >= _MIN_SECTION_CHARS else section


def _risk_text(filing: dict, max_chars: int) -> tuple[str, str]:
    """(text, source): the 10-K's Item 1A section from EDGAR, else the stored text's start."""
    from data import edgar_docs

    section = None
    if filing.get("filing_url"):
        try:
            md = edgar_docs.fetch_filing_markdown(
                filing["filing_url"], filing["accession_no"], "10-K"
            )
            section = extract_risk_factors(md)
        except Exception as exc:
            logger.info("Risk: EDGAR fetch failed for %s: %s", filing["accession_no"], exc)
    if section and len(section) >= _MIN_SECTION_CHARS:
        logger.info("Risk: using Item 1A from EDGAR for %s", filing["accession_no"])
        return _MD_ESCAPE.sub(r"\1", section)[:max_chars], "edgar"
    logger.info("Risk: Item 1A not found for %s; using stored 10-K text", filing["accession_no"])
    return _strip_html(filing["content_text"] or "")[:max_chars], "stored"


def _strip_html(text: str) -> str:
    return _HTML_TAG_RE.sub(" ", text).strip()


def _validate(result: dict) -> dict:
    if result.get("risk_severity") not in _SEVERITY_MAP:
        result["risk_severity"] = "MEDIUM"
    bp = result.get("boilerplate_percentage", 0)
    result["boilerplate_percentage"] = max(0, min(100, int(bp)))
    return result


def _zero_usage():
    from unittest.mock import MagicMock

    u = MagicMock()
    u.prompt_tokens = 0
    u.completion_tokens = 0
    return u


def _last_cost(client: OpenAIClient) -> float:
    calls = client._tracker._calls
    return calls[-1]["cost_usd"] if calls else 0.0
