"""Tests for packet 31: risk score from the severity mix of material risks."""

from __future__ import annotations

import pytest

import analysis.db  # noqa: F401 — registers tables
from analysis.cache import AnalysisCache
from analysis.combined_score import compute_ai_composite
from analysis.risk_analyzer import risk_score


def _result(*severities, label="HIGH"):
    return {
        "material_risks": [{"risk": f"r{i}", "severity": s} for i, s in enumerate(severities)],
        "risk_severity": label,
    }


class TestRiskScore:
    def test_mean_of_severities(self):
        # LOW 0, MEDIUM 1, HIGH 2, CRITICAL 3 -> mean 1.5 -> 10 - 3 = 7
        assert risk_score(_result("LOW", "MEDIUM", "HIGH", "CRITICAL")) == pytest.approx(7.0)

    def test_all_low_is_ten(self):
        assert risk_score(_result("LOW", "LOW")) == pytest.approx(10.0)

    def test_all_critical_is_four(self):
        assert risk_score(_result("CRITICAL")) == pytest.approx(4.0)

    def test_invalid_severities_ignored(self):
        result = _result("HIGH", "SEVERE", None, "low", "MEDIUM")
        assert risk_score(result) == pytest.approx(10 - 2 * 1.5)

    def test_none_result(self):
        assert risk_score(None) is None

    def test_empty_list(self):
        assert risk_score({"material_risks": [], "risk_severity": "LOW"}) is None

    def test_missing_list(self):
        assert risk_score({"risk_severity": "LOW"}) is None

    def test_not_a_list(self):
        assert risk_score({"material_risks": "HIGH", "risk_severity": "LOW"}) is None

    def test_no_valid_entries(self):
        assert risk_score(_result("UNKNOWN", "", label="LOW")) is None

    def test_label_not_used_as_fallback(self):
        # Label says CRITICAL, entries say MEDIUM: the entries decide.
        assert risk_score(_result("MEDIUM", "MEDIUM", label="CRITICAL")) == pytest.approx(8.0)

    # Adversarial cases beyond the spec edge cases.
    def test_entries_that_are_not_dicts_ignored(self):
        result = {"material_risks": ["HIGH", 3, ["LOW"], {"severity": "LOW"}]}
        assert risk_score(result) == pytest.approx(10.0)

    def test_unhashable_severity_ignored(self):
        result = {"material_risks": [{"severity": ["HIGH"]}, {"severity": "HIGH"}]}
        assert risk_score(result) == pytest.approx(6.0)

    def test_many_entries_stays_in_range(self):
        score = risk_score(_result(*(["CRITICAL"] * 50 + ["LOW"])))
        assert 4.0 <= score <= 10.0


class TestCompositeWithoutRisk:
    def test_empty_risks_excluded_from_composite(self, tmp_db):
        filing = {"earnings_quality_score": 8.0, "balance_sheet_score": 6.0}
        risk = {"material_risks": [], "risk_severity": "LOW"}
        result = compute_ai_composite(tmp_db, "CLX", "2026-10-15", None, filing, risk, None)
        assert result["risk_score"] is None
        assert result["analyzers_used"] == 1
        assert result["ai_composite"] == pytest.approx(7.0)


class TestCacheKey:
    def test_artifact_id_unchanged(self):
        assert AnalysisCache.artifact_id_risk("AAPL", "0000320193-25-000079") == (
            "AAPL_0000320193-25-000079_v2"
        )
