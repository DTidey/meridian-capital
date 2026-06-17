"""Tests for analysis/cache.py."""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import analysis.db  # noqa: F401 — registers tables
from analysis.cache import AnalysisCache
from analysis.db import analysis_results
from data.db import get_engine, initialise_schema


@pytest.fixture
def tmp_engine(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path / 'test.db'}")
    initialise_schema(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def tmp_db(tmp_engine):
    conn = tmp_engine.connect()
    yield conn
    conn.close()


def _fake_usage(prompt=100, completion=50):
    u = MagicMock()
    u.prompt_tokens = prompt
    u.completion_tokens = completion
    return u


class TestGetMiss:
    def test_empty_cache_returns_none(self, tmp_db):
        cache = AnalysisCache(tmp_db)
        assert cache.get("earnings", "AAPL", "AAPL_2024-03-31") is None

    def test_expired_entry_returns_none(self, tmp_db):
        # Insert an already-expired row directly
        past = (datetime.now(UTC) - timedelta(days=1)).isoformat(timespec="seconds")
        tmp_db.execute(
            analysis_results.insert().values(
                analyzer="earnings",
                ticker="AAPL",
                artifact_id="AAPL_2024-03-31",
                model="gpt-4o",
                result_json='{"score": 7}',
                prompt_tokens=100,
                completion_tokens=50,
                cost_usd=0.001,
                created_at=past,
                expires_at=past,
            )
        )
        tmp_db.commit()
        cache = AnalysisCache(tmp_db)
        assert cache.get("earnings", "AAPL", "AAPL_2024-03-31") is None

    def test_different_artifact_id_returns_none(self, tmp_db):
        cache = AnalysisCache(tmp_db)
        cache.set(
            "earnings", "AAPL", "AAPL_2024-03-31", "gpt-4o", {"score": 7}, _fake_usage(), 0.001
        )
        assert cache.get("earnings", "AAPL", "AAPL_2024-06-30") is None


class TestGetHit:
    def test_fresh_entry_returned(self, tmp_db):
        cache = AnalysisCache(tmp_db, ttl_days=30)
        result = {"score": 8, "reasoning": "strong guidance"}
        cache.set("earnings", "AAPL", "AAPL_2024-03-31", "gpt-4o", result, _fake_usage(), 0.002)
        hit = cache.get("earnings", "AAPL", "AAPL_2024-03-31")
        assert hit == result

    def test_different_analyzer_miss(self, tmp_db):
        cache = AnalysisCache(tmp_db)
        cache.set(
            "earnings", "AAPL", "AAPL_2024-03-31", "gpt-4o", {"score": 7}, _fake_usage(), 0.001
        )
        assert cache.get("filing", "AAPL", "AAPL_2024-03-31") is None

    def test_different_ticker_miss(self, tmp_db):
        cache = AnalysisCache(tmp_db)
        cache.set(
            "earnings", "AAPL", "AAPL_2024-03-31", "gpt-4o", {"score": 7}, _fake_usage(), 0.001
        )
        assert cache.get("earnings", "MSFT", "AAPL_2024-03-31") is None


class TestSet:
    def test_upsert_overwrites_existing(self, tmp_db):
        cache = AnalysisCache(tmp_db)
        cache.set(
            "earnings", "AAPL", "AAPL_2024-03-31", "gpt-4o", {"score": 5}, _fake_usage(), 0.001
        )
        cache.set(
            "earnings", "AAPL", "AAPL_2024-03-31", "gpt-4o", {"score": 9}, _fake_usage(), 0.001
        )
        hit = cache.get("earnings", "AAPL", "AAPL_2024-03-31")
        assert hit["score"] == 9


class TestEviction:
    def test_evicts_expired_rows(self, tmp_db):
        past = (datetime.now(UTC) - timedelta(days=1)).isoformat(timespec="seconds")
        for i in range(3):
            tmp_db.execute(
                analysis_results.insert().values(
                    analyzer="risk",
                    ticker=f"T{i}",
                    artifact_id=f"T{i}_x",
                    model="gpt-4o",
                    result_json="{}",
                    prompt_tokens=10,
                    completion_tokens=10,
                    cost_usd=0.0,
                    created_at=past,
                    expires_at=past,
                )
            )
        tmp_db.commit()
        cache = AnalysisCache(tmp_db)
        count = cache.evict_expired()
        assert count == 3

    def test_does_not_evict_fresh_rows(self, tmp_db):
        cache = AnalysisCache(tmp_db, ttl_days=30)
        cache.set("risk", "AAPL", "AAPL_0001", "gpt-4o", {"ok": True}, _fake_usage(), 0.0)
        assert cache.evict_expired() == 0


class TestArtifactIds:
    def test_earnings_artifact_id(self):
        assert AnalysisCache.artifact_id_earnings("AAPL", "2024-03-31") == "AAPL_2024-03-31"

    def test_filing_artifact_id(self):
        assert AnalysisCache.artifact_id_filing("MSFT", "2024-06-30") == "MSFT_2024-06-30"

    def test_risk_artifact_id(self):
        assert (
            AnalysisCache.artifact_id_risk("GOOG", "0001234567-24-000001")
            == "GOOG_0001234567-24-000001"
        )

    def test_insider_artifact_id(self):
        assert AnalysisCache.artifact_id_insider("NVDA", "2024-06-01") == "NVDA_2024-06-01"
