"""10-K risk factor analyzer."""

import logging
import re

import sqlalchemy as sa

from analysis.api_client import OpenAIClient
from analysis.cache import AnalysisCache
from data.db import sec_filings

logger = logging.getLogger(__name__)

_SEVERITY_MAP  = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
_SEVERITY_SCORE = {k: 10 - v * 2 for k, v in _SEVERITY_MAP.items()}   # LOW→10, MEDIUM→8, HIGH→6, CRITICAL→4

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
    artifact_id  = AnalysisCache.artifact_id_risk(ticker, accession_no)

    cached = cache.get("risk", ticker, artifact_id)
    if cached is not None:
        logger.debug("Risk: cache hit for %s (%s)", ticker, accession_no)
        return cached

    max_chars = config.get("analysis", {}).get("filing_risk_max_chars", 80_000)
    text      = _strip_html(filing["content_text"] or "")[:max_chars]
    model     = config.get("analysis", {}).get("analyzer_models", {}).get("risk",
                config.get("analysis", {}).get("openai_model", "gpt-4o"))

    prior_note = ""
    prior = _fetch_prior_10k(conn, ticker, accession_no, score_date)
    if prior:
        prior_note = f"\nPrior filing accession: {prior['accession_no']} (filed {prior['filed_date']})"

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

    cache.set("risk", ticker, artifact_id, model, result,
              _zero_usage(), _last_cost(client))
    return result


def risk_score(result: dict) -> float | None:
    """Map risk_severity to a 1–10 score (lower risk → higher score)."""
    if result is None:
        return None
    severity = result.get("risk_severity", "MEDIUM")
    return float(_SEVERITY_SCORE.get(severity, 8))


def _fetch_10k(conn, ticker: str, score_date: str) -> dict | None:
    row = conn.execute(
        sa.select(
            sec_filings.c.accession_no,
            sec_filings.c.filed_date,
            sec_filings.c.content_text,
        ).where(
            (sec_filings.c.ticker    == ticker) &
            (sec_filings.c.form_type == "10-K") &
            (sec_filings.c.filed_date <= score_date)
        ).order_by(sec_filings.c.filed_date.desc())
        .limit(1)
    ).fetchone()
    if row is None:
        return None
    return {"accession_no": row[0], "filed_date": row[1], "content_text": row[2]}


def _fetch_prior_10k(conn, ticker: str, current_accession: str, score_date: str) -> dict | None:
    row = conn.execute(
        sa.select(
            sec_filings.c.accession_no,
            sec_filings.c.filed_date,
        ).where(
            (sec_filings.c.ticker      == ticker) &
            (sec_filings.c.form_type   == "10-K") &
            (sec_filings.c.accession_no != current_accession) &
            (sec_filings.c.filed_date   <= score_date)
        ).order_by(sec_filings.c.filed_date.desc())
        .limit(1)
    ).fetchone()
    if row is None:
        return None
    return {"accession_no": row[0], "filed_date": row[1]}


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
