"""Insider trading signal analyzer (Form 4)."""

import logging
from datetime import date, timedelta

import sqlalchemy as sa

from analysis.api_client import OpenAIClient
from analysis.cache import AnalysisCache
from data.db import insider_transactions, insider_cluster_flags

logger = logging.getLogger(__name__)

_SIGNAL_SCORE = {
    "STRONG_BUY":    10.0,
    "MODERATE_BUY":   7.5,
    "NEUTRAL":        5.0,
    "MODERATE_SELL":  2.5,
    "STRONG_SELL":    1.0,
}

_SYSTEM_PROMPT = """\
You are an equity analyst interpreting insider trading signals from SEC Form 4 filings.
Distinguish between routine selling (diversification, options exercise proceeds) and
meaningful open-market buying. CEO/CFO activity is the most informative.
Your response MUST be a valid JSON object with no additional text."""

_RESPONSE_SCHEMA = """\
{
  "signal_strength":  "STRONG_BUY|MODERATE_BUY|NEUTRAL|MODERATE_SELL|STRONG_SELL",
  "confidence":       "LOW|MEDIUM|HIGH",
  "key_transactions": ["<description>", ...],
  "reasoning":        "<string>",
  "one_line_summary": "<string>"
}"""

_LOOKBACK_DAYS = 90


def analyse(
    conn: sa.engine.Connection,
    ticker: str,
    client: OpenAIClient,
    cache: AnalysisCache,
    config: dict,
    score_date: str,
) -> dict | None:
    """Analyse insider Form 4 transactions for ticker within the 90-day lookback."""
    window_start = _lookback_start(score_date)
    txns = _fetch_transactions(conn, ticker, window_start, score_date)
    if not txns:
        logger.debug("Insider: no open-market transactions for %s", ticker)
        return None

    artifact_id = AnalysisCache.artifact_id_insider(ticker, score_date)
    cached = cache.get("insider", ticker, artifact_id)
    if cached is not None:
        logger.debug("Insider: cache hit for %s", ticker)
        return cached

    model = config.get("analysis", {}).get("analyzer_models", {}).get("insider",
            config.get("analysis", {}).get("openai_model_cheap", "gpt-4o-mini"))

    cluster = _fetch_cluster(conn, ticker, window_start, score_date)
    txn_text = _format_transactions(txns, cluster)

    user_prompt = (
        f"Company: {ticker}\n"
        f"Analysis window: {window_start} to {score_date}\n\n"
        f"INSIDER TRANSACTIONS (open-market only):\n{txn_text}\n\n"
        f"Return your assessment as JSON matching this schema:\n{_RESPONSE_SCHEMA}"
    )

    logger.info("Insider: calling API for %s (model=%s)", ticker, model)
    result = client.chat(_SYSTEM_PROMPT, user_prompt, model=model)
    result = _validate(result)

    cache.set("insider", ticker, artifact_id, model, result,
              _zero_usage(), _last_cost(client))
    return result


def insider_score(result: dict) -> float | None:
    """Map signal_strength to a 1–10 score."""
    if result is None:
        return None
    signal = result.get("signal_strength", "NEUTRAL")
    return _SIGNAL_SCORE.get(signal, 5.0)


def _lookback_start(score_date: str) -> str:
    d = date.fromisoformat(score_date)
    return str(d - timedelta(days=_LOOKBACK_DAYS))


def _fetch_transactions(conn, ticker: str, window_start: str, score_date: str) -> list[dict]:
    rows = conn.execute(
        sa.select(
            insider_transactions.c.insider_name,
            insider_transactions.c.insider_title,
            insider_transactions.c.transaction_type,
            insider_transactions.c.shares,
            insider_transactions.c.price,
            insider_transactions.c.date,
            insider_transactions.c.is_ceo_cfo,
        ).where(
            (insider_transactions.c.ticker       == ticker) &
            (insider_transactions.c.is_open_market == 1) &
            (insider_transactions.c.date          >= window_start) &
            (insider_transactions.c.date          <= score_date)
        ).order_by(insider_transactions.c.date.desc())
        .limit(50)
    ).fetchall()

    return [
        {
            "name":       row[0],
            "title":      row[1],
            "type":       row[2],
            "shares":     row[3],
            "price":      row[4],
            "date":       row[5],
            "is_ceo_cfo": bool(row[6]),
        }
        for row in rows
    ]


def _fetch_cluster(conn, ticker: str, window_start: str, score_date: str) -> dict | None:
    row = conn.execute(
        sa.select(
            insider_cluster_flags.c.insider_count,
            insider_cluster_flags.c.total_shares,
            insider_cluster_flags.c.window_start,
            insider_cluster_flags.c.window_end,
        ).where(
            (insider_cluster_flags.c.ticker       == ticker) &
            (insider_cluster_flags.c.window_start >= window_start) &
            (insider_cluster_flags.c.window_end   <= score_date)
        ).order_by(insider_cluster_flags.c.window_start.desc())
        .limit(1)
    ).fetchone()

    if row is None:
        return None
    return {
        "insider_count": row[0],
        "total_shares":  row[1],
        "window_start":  row[2],
        "window_end":    row[3],
    }


def _format_transactions(txns: list[dict], cluster: dict | None) -> str:
    lines = []
    for t in txns:
        ceo_flag = " [CEO/CFO]" if t["is_ceo_cfo"] else ""
        price_str = f" @ ${t['price']:.2f}" if t["price"] else ""
        lines.append(
            f"  {t['date']}  {t['name']}{ceo_flag} ({t['title']})  "
            f"{t['type']}  {t['shares']:,.0f} shares{price_str}"
        )
    text = "\n".join(lines)
    if cluster:
        text += (
            f"\n\nCLUSTER FLAG: {cluster['insider_count']} insiders traded "
            f"{cluster['total_shares']:,.0f} total shares between "
            f"{cluster['window_start']} and {cluster['window_end']}"
        )
    return text


def _validate(result: dict) -> dict:
    if result.get("signal_strength") not in _SIGNAL_SCORE:
        result["signal_strength"] = "NEUTRAL"
    if result.get("confidence") not in ("LOW", "MEDIUM", "HIGH"):
        result["confidence"] = "MEDIUM"
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
