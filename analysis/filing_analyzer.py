"""Forensic filing analyzer — uses 8 quarters of fundamental metrics."""

import logging

import pandas as pd
import sqlalchemy as sa

from analysis.api_client import OpenAIClient
from analysis.cache import AnalysisCache
from data.db import fundamentals as fundamentals_table

logger = logging.getLogger(__name__)

_RISK_LEVELS = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}

_SYSTEM_PROMPT = """\
You are a forensic accounting analyst reviewing quarterly financial data.
Assess accounting quality, focusing on earnings quality (CFO vs NI), revenue patterns
(AR vs revenue growth), balance sheet health, and accruals.
Score 1 to 10 where 10 is the highest quality.
Flag specific red flags and green flags with evidence.
Your response MUST be a valid JSON object with no additional text."""

_RESPONSE_SCHEMA = """\
{
  "earnings_quality_score": <1-10>,
  "balance_sheet_score":    <1-10>,
  "green_flags": ["<flag1>", ...],
  "red_flags":   ["<flag1>", ...],
  "risk_level":  "LOW|MEDIUM|HIGH|CRITICAL",
  "reasoning":   "<string>",
  "one_line_summary": "<string>"
}"""

_FUND_COLS = [
    "period_end",
    "revenue",
    "net_income",
    "cfo",
    "total_assets",
    "total_debt",
    "total_equity",
    "accounts_receivable",
    "gross_margin",
    "operating_margin",
    "debt_to_equity",
    "cfo_to_ni",
    "accruals_ratio",
    "current_ratio",
]


def analyse(
    conn: sa.engine.Connection,
    ticker: str,
    client: OpenAIClient,
    cache: AnalysisCache,
    config: dict,
    score_date: str,
) -> dict | None:
    """Analyse 8 quarters of fundamentals for ticker."""
    fund_df = _fetch_fundamentals(conn, ticker, score_date)
    if fund_df.empty:
        logger.debug("Filing: no fundamentals for %s", ticker)
        return None

    latest_period = str(fund_df["period_end"].max())[:10]
    artifact_id = AnalysisCache.artifact_id_filing(ticker, latest_period)

    cached = cache.get("filing", ticker, artifact_id)
    if cached is not None:
        logger.debug("Filing: cache hit for %s (%s)", ticker, latest_period)
        return cached

    model = (
        config.get("analysis", {})
        .get("analyzer_models", {})
        .get("filing", config.get("analysis", {}).get("openai_model_cheap", "gpt-4o-mini"))
    )

    table_json = fund_df.tail(8).to_json(orient="records", date_format="iso")
    user_prompt = (
        f"Company: {ticker}\n"
        f"Most recent period: {latest_period}\n\n"
        f"QUARTERLY FINANCIAL DATA (last 8 quarters, oldest first):\n{table_json}\n\n"
        f"Return your assessment as JSON matching this schema:\n{_RESPONSE_SCHEMA}"
    )

    logger.info("Filing: calling API for %s (model=%s)", ticker, model)
    result = client.chat(_SYSTEM_PROMPT, user_prompt, model=model)
    result = _validate(result)

    cache.set("filing", ticker, artifact_id, model, result, _zero_usage(), _last_cost(client))
    return result


def filing_score(result: dict) -> float | None:
    """Return mean of earnings_quality_score and balance_sheet_score (1–10)."""
    if result is None:
        return None
    eq = result.get("earnings_quality_score")
    bs = result.get("balance_sheet_score")
    scores = [float(s) for s in [eq, bs] if s is not None]
    return sum(scores) / len(scores) if scores else None


def _fetch_fundamentals(conn, ticker: str, score_date: str) -> pd.DataFrame:
    rows = conn.execute(
        sa.select(
            *[
                fundamentals_table.c[c]
                for c in _FUND_COLS
                if c in [col.name for col in fundamentals_table.columns]
            ]
        )
        .where(
            (fundamentals_table.c.ticker == ticker)
            & (fundamentals_table.c.period_type == "quarterly")
            & (fundamentals_table.c.period_end <= score_date)
        )
        .order_by(fundamentals_table.c.period_end.desc())
        .limit(8)
    ).fetchall()

    available = [col.name for col in fundamentals_table.columns if col.name in _FUND_COLS]
    df = pd.DataFrame(rows, columns=available)
    return df.sort_values("period_end") if not df.empty else df


def _validate(result: dict) -> dict:
    for col in ("earnings_quality_score", "balance_sheet_score"):
        if col in result:
            result[col] = max(1, min(10, float(result[col])))
    if result.get("risk_level") not in _RISK_LEVELS:
        result["risk_level"] = "MEDIUM"
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
