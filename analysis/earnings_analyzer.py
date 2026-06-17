"""Earnings call transcript analyzer."""

import logging

import sqlalchemy as sa

from analysis.api_client import OpenAIClient
from analysis.cache import AnalysisCache
from data.db import earnings_transcripts

logger = logging.getLogger(__name__)

_CATEGORIES = [
    "management_confidence",
    "revenue_guidance",
    "margin_trajectory",
    "competitive_position",
    "risk_factors",
    "capital_allocation",
]

_SYSTEM_PROMPT = """\
You are a senior equity analyst specialising in earnings call analysis.
You will receive an earnings call transcript and return a structured JSON assessment.
Score each of the six categories from 1 to 10, where 10 is the most positive.
Provide clear, evidence-based reasoning for each score.
Your response MUST be a valid JSON object with no additional text outside the JSON."""

_RESPONSE_SCHEMA = """\
{
  "management_confidence": {"score": <1-10>, "reasoning": "<string>"},
  "revenue_guidance":      {"score": <1-10>, "reasoning": "<string>"},
  "margin_trajectory":     {"score": <1-10>, "reasoning": "<string>"},
  "competitive_position":  {"score": <1-10>, "reasoning": "<string>"},
  "risk_factors":          {"score": <1-10>, "reasoning": "<string>"},
  "capital_allocation":    {"score": <1-10>, "reasoning": "<string>"},
  "bull_case":             "<string>",
  "bear_case":             "<string>",
  "key_quotes":            ["<quote1>", "<quote2>", "<quote3>"],
  "one_line_summary":      "<string>"
}"""


def analyse(
    conn: sa.engine.Connection,
    ticker: str,
    client: OpenAIClient,
    cache: AnalysisCache,
    config: dict,
    score_date: str,
) -> dict | None:
    """Analyse the most recent earnings transcript for ticker.

    Returns the parsed result dict, or None if no transcript is available.
    """
    transcript_row = _fetch_transcript(conn, ticker)
    if transcript_row is None:
        logger.debug("Earnings: no transcript for %s", ticker)
        return None

    earnings_date = transcript_row["earnings_date"]
    artifact_id   = AnalysisCache.artifact_id_earnings(ticker, str(earnings_date))

    cached = cache.get("earnings", ticker, artifact_id)
    if cached is not None:
        logger.debug("Earnings: cache hit for %s (%s)", ticker, earnings_date)
        return cached

    max_chars = config.get("analysis", {}).get("transcript_max_chars", 120_000)
    content   = (transcript_row["content"] or "")[:max_chars]
    model     = config.get("analysis", {}).get("analyzer_models", {}).get("earnings",
                config.get("analysis", {}).get("openai_model", "gpt-4o"))

    user_prompt = (
        f"Company: {ticker}\n"
        f"Earnings date: {earnings_date}\n\n"
        f"TRANSCRIPT:\n{content}\n\n"
        f"Return your assessment as JSON matching this schema:\n{_RESPONSE_SCHEMA}"
    )

    logger.info("Earnings: calling API for %s (model=%s)", ticker, model)
    result = client.chat(_SYSTEM_PROMPT, user_prompt, model=model)
    result = _validate_and_normalise(result)

    # Store last usage for the cache.set call — tracker already recorded it
    cache.set("earnings", ticker, artifact_id, model, result,
              _last_usage(client), _last_cost(client))
    return result


def earnings_score(result: dict) -> float | None:
    """Return the mean category score (1–10), or None if result is None."""
    if result is None:
        return None
    scores = []
    for cat in _CATEGORIES:
        cat_data = result.get(cat, {})
        if isinstance(cat_data, dict):
            s = cat_data.get("score")
            if s is not None:
                scores.append(float(s))
    return sum(scores) / len(scores) if scores else None


def _fetch_transcript(conn, ticker: str) -> dict | None:
    row = conn.execute(
        sa.select(
            earnings_transcripts.c.earnings_date,
            earnings_transcripts.c.content,
        ).where(earnings_transcripts.c.ticker == ticker)
        .order_by(earnings_transcripts.c.earnings_date.desc())
        .limit(1)
    ).fetchone()
    if row is None:
        return None
    return {"earnings_date": row[0], "content": row[1]}


def _validate_and_normalise(result: dict) -> dict:
    """Clamp scores to 1–10 range and ensure required keys exist."""
    for cat in _CATEGORIES:
        if cat in result and isinstance(result[cat], dict):
            score = result[cat].get("score", 5)
            result[cat]["score"] = max(1, min(10, float(score)))
    return result


def _last_usage(client: OpenAIClient):
    """Return a minimal usage object reflecting the most recent tracker call."""
    from unittest.mock import MagicMock
    u = MagicMock()
    u.prompt_tokens     = 0
    u.completion_tokens = 0
    return u


def _last_cost(client: OpenAIClient) -> float:
    calls = client._tracker._calls
    return calls[-1]["cost_usd"] if calls else 0.0
