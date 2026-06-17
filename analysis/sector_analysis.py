"""Per-sector AI ranking and outlook."""

import json
import logging
from collections import defaultdict

from analysis.api_client import OpenAIClient

logger = logging.getLogger(__name__)

_SECTOR_OUTLOOKS = frozenset(["VERY_POSITIVE", "POSITIVE", "NEUTRAL", "NEGATIVE", "VERY_NEGATIVE"])

_SYSTEM_PROMPT = """\
You are a sector analyst. Given quantitative scores and qualitative AI assessments
for a set of companies in the same sector, rank them from strongest LONG to strongest
SHORT and provide a sector outlook. Your response MUST be a valid JSON object."""

_RESPONSE_SCHEMA = """\
{
  "sector": "<string>",
  "rankings": [
    {"ticker": "<string>", "rank": <integer>, "rationale": "<string>"},
    ...
  ],
  "top_long_idea":    {"ticker": "<string>", "thesis": "<string>"},
  "top_short_idea":   {"ticker": "<string>", "thesis": "<string>"},
  "sector_outlook":   "VERY_POSITIVE|POSITIVE|NEUTRAL|NEGATIVE|VERY_NEGATIVE",
  "sector_reasoning": "<string>"
}"""


def analyse_sectors(
    candidates: list[dict],
    analyzer_results: dict[str, dict],
    client: OpenAIClient,
    config: dict,
) -> dict[str, dict]:
    """Run sector analysis for all sectors with ≥2 candidates.

    Args:
        candidates: List of dicts with keys: ticker, sector, composite_score, direction.
        analyzer_results: Dict mapping ticker → {"earnings": ..., "filing": ..., "risk": ..., "insider": ...}
        client: OpenAI API client.
        config: Application config.

    Returns:
        Dict mapping sector name → sector analysis result dict.
    """
    model = config.get("analysis", {}).get("openai_model", "gpt-4o")
    by_sector = _group_by_sector(candidates)

    results = {}
    for sector, tickers in by_sector.items():
        if len(tickers) < 2:
            logger.debug("Sector: skipping %s (only %d candidate)", sector, len(tickers))
            continue

        sector_candidates = [c for c in candidates if c["ticker"] in tickers]
        result = _analyse_sector(sector, sector_candidates, analyzer_results, client, model)
        if result is not None:
            results[sector] = result

    return results


def _analyse_sector(
    sector: str,
    candidates: list[dict],
    analyzer_results: dict[str, dict],
    client: OpenAIClient,
    model: str,
) -> dict | None:
    summary = _build_sector_summary(sector, candidates, analyzer_results)
    user_prompt = (
        f"Sector: {sector}\n"
        f"Number of candidates: {len(candidates)}\n\n"
        f"CANDIDATE SUMMARY:\n{summary}\n\n"
        f"Return your assessment as JSON matching this schema:\n{_RESPONSE_SCHEMA}"
    )

    logger.info("Sector: calling API for %s (%d candidates)", sector, len(candidates))
    try:
        result = client.chat(_SYSTEM_PROMPT, user_prompt, model=model)
        return _validate(result, sector)
    except Exception as exc:
        logger.warning("Sector: API call failed for %s: %s", sector, exc)
        return None


def _group_by_sector(candidates: list[dict]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for c in candidates:
        sector = c.get("sector") or "Unknown"
        groups[sector].append(c["ticker"])
    return dict(groups)


def _build_sector_summary(
    sector: str,
    candidates: list[dict],
    analyzer_results: dict[str, dict],
) -> str:
    rows = []
    for c in sorted(candidates, key=lambda x: x.get("composite_score", 0), reverse=True):
        ticker = c["ticker"]
        quant = c.get("composite_score", "N/A")
        direction = c.get("direction", "NEUTRAL")
        ai = analyzer_results.get(ticker, {})

        entry = {
            "ticker": ticker,
            "direction": direction,
            "quant_composite": quant,
        }
        if ai.get("earnings"):
            entry["earnings_summary"] = ai["earnings"].get("one_line_summary")
        if ai.get("filing"):
            entry["filing_summary"] = ai["filing"].get("one_line_summary")
        if ai.get("risk"):
            entry["risk_summary"] = ai["risk"].get("one_line_summary")
            entry["risk_severity"] = ai["risk"].get("risk_severity")
        if ai.get("insider"):
            entry["insider_signal"] = ai["insider"].get("signal_strength")
        rows.append(entry)

    return json.dumps(rows, indent=2)


def _validate(result: dict, sector: str) -> dict:
    if result.get("sector") != sector:
        result["sector"] = sector
    if result.get("sector_outlook") not in _SECTOR_OUTLOOKS:
        result["sector_outlook"] = "NEUTRAL"
    if not isinstance(result.get("rankings"), list):
        result["rankings"] = []
    return result
