"""PostgreSQL-backed analysis result cache with TTL eviction."""

import json
import logging
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from analysis.db import analysis_results
from data.db import insert_or_replace

logger = logging.getLogger(__name__)


class AnalysisCache:
    """Read/write cache for AI analyzer results keyed by (analyzer, ticker, artifact_id)."""

    def __init__(self, conn: sa.engine.Connection, ttl_days: int = 30) -> None:
        self._conn = conn
        self._ttl = ttl_days

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get(self, analyzer: str, ticker: str, artifact_id: str) -> dict | None:
        """Return cached result dict if present and not expired, else None."""
        now = _utcnow_iso()
        row = self._conn.execute(
            sa.select(analysis_results.c.result_json).where(
                (analysis_results.c.analyzer == analyzer)
                & (analysis_results.c.ticker == ticker)
                & (analysis_results.c.artifact_id == artifact_id)
                & (analysis_results.c.expires_at > now)
            )
        ).fetchone()

        if row is None:
            return None

        try:
            return json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "Cache: corrupt JSON for %s/%s/%s — treating as miss", analyzer, ticker, artifact_id
            )
            return None

    def set(
        self,
        analyzer: str,
        ticker: str,
        artifact_id: str,
        model: str,
        result: dict,
        usage,  # openai CompletionUsage object
        cost: float,
    ) -> None:
        """Upsert a result into the cache."""
        now = _utcnow_iso()
        expires = _utcnow_plus(self._ttl)

        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0

        stmt = insert_or_replace(self._conn, analysis_results)
        self._conn.execute(
            stmt,
            [
                {
                    "analyzer": analyzer,
                    "ticker": ticker,
                    "artifact_id": artifact_id,
                    "model": model,
                    "result_json": json.dumps(result),
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "cost_usd": cost,
                    "created_at": now,
                    "expires_at": expires,
                }
            ],
        )
        self._conn.commit()
        logger.debug("Cache: stored %s/%s/%s (expires %s)", analyzer, ticker, artifact_id, expires)

    def evict_expired(self) -> int:
        """Delete all rows past their TTL; return count deleted."""
        now = _utcnow_iso()
        result = self._conn.execute(
            analysis_results.delete().where(analysis_results.c.expires_at <= now)
        )
        self._conn.commit()
        count = result.rowcount
        if count:
            logger.info("Cache: evicted %d expired entries", count)
        return count

    # ------------------------------------------------------------------
    # artifact_id helpers (one per analyzer type)
    # ------------------------------------------------------------------

    @staticmethod
    def artifact_id_earnings(ticker: str, earnings_date: str) -> str:
        return f"{ticker}_{earnings_date}"

    @staticmethod
    def artifact_id_filing(ticker: str, period_end: str) -> str:
        return f"{ticker}_{period_end}"

    @staticmethod
    def artifact_id_risk(ticker: str, accession_no: str) -> str:
        return f"{ticker}_{accession_no}"

    @staticmethod
    def artifact_id_insider(ticker: str, score_date: str) -> str:
        return f"{ticker}_{score_date}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _utcnow_plus(days: int) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat(timespec="seconds")
