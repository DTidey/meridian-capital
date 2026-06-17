"""Tests for analysis/cost_tracker.py."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from analysis.cost_tracker import _PRICING, CostTracker


def _mock_usage(prompt=100, completion=50, cached=0):
    usage = MagicMock()
    usage.prompt_tokens = prompt
    usage.completion_tokens = completion
    details = MagicMock()
    details.cached_tokens = cached
    usage.prompt_tokens_details = details
    return usage


class TestRecord:
    def test_records_cost_correctly_gpt4o(self):
        tracker = CostTracker()
        usage = _mock_usage(prompt=1_000_000, completion=0)
        cost = tracker.record(usage, "gpt-4o")
        assert cost == pytest.approx(_PRICING["gpt-4o"]["input"])

    def test_records_cost_correctly_mini(self):
        tracker = CostTracker()
        usage = _mock_usage(prompt=1_000_000, completion=0)
        cost = tracker.record(usage, "gpt-4o-mini")
        assert cost == pytest.approx(_PRICING["gpt-4o-mini"]["input"])

    def test_cached_tokens_at_half_price(self):
        tracker = CostTracker()
        # 1M cached tokens should cost half of 1M regular tokens
        full = _mock_usage(prompt=1_000_000, completion=0, cached=0)
        cached = _mock_usage(prompt=1_000_000, completion=0, cached=1_000_000)
        cost_full = tracker.record(full, "gpt-4o")
        cost_cached = tracker.record(cached, "gpt-4o")
        assert cost_cached == pytest.approx(cost_full / 2)

    def test_output_tokens_costed_separately(self):
        tracker = CostTracker()
        usage = _mock_usage(prompt=0, completion=1_000_000)
        cost = tracker.record(usage, "gpt-4o")
        assert cost == pytest.approx(_PRICING["gpt-4o"]["output"])

    def test_unknown_model_uses_default(self):
        tracker = CostTracker()
        usage = _mock_usage(prompt=1_000, completion=0)
        # Should not raise
        cost = tracker.record(usage, "gpt-unknown-model")
        assert cost >= 0

    def test_accumulates_across_calls(self):
        tracker = CostTracker()
        for _ in range(3):
            tracker.record(_mock_usage(prompt=100, completion=50), "gpt-4o-mini")
        assert tracker.total_cost_usd() == pytest.approx(
            3 * CostTracker.estimate_cost(100, "gpt-4o-mini")
            + 3 * (50 / 1_000_000 * _PRICING["gpt-4o-mini"]["output"])
        )


class TestTotals:
    def test_total_prompt_tokens(self):
        tracker = CostTracker()
        tracker.record(_mock_usage(prompt=100), "gpt-4o")
        tracker.record(_mock_usage(prompt=200), "gpt-4o")
        assert tracker.total_prompt_tokens() == 300

    def test_total_completion_tokens(self):
        tracker = CostTracker()
        tracker.record(_mock_usage(completion=50), "gpt-4o")
        tracker.record(_mock_usage(completion=75), "gpt-4o")
        assert tracker.total_completion_tokens() == 125

    def test_total_cached_tokens(self):
        tracker = CostTracker()
        tracker.record(_mock_usage(prompt=200, cached=100), "gpt-4o")
        tracker.record(_mock_usage(prompt=200, cached=50), "gpt-4o")
        assert tracker.total_cached_tokens() == 150

    def test_empty_tracker_returns_zeros(self):
        tracker = CostTracker()
        assert tracker.total_cost_usd() == pytest.approx(0.0)
        assert tracker.total_prompt_tokens() == 0


class TestCeiling:
    def test_no_exceed_below_ceiling(self):
        tracker = CostTracker(ceiling_usd=10.0)
        assert not tracker.would_exceed_ceiling(0, "gpt-4o")

    def test_would_exceed_at_ceiling(self):
        tracker = CostTracker(ceiling_usd=0.001)
        tracker.record(_mock_usage(prompt=1_000_000), "gpt-4o")
        assert tracker.would_exceed_ceiling(0, "gpt-4o")

    def test_estimated_tokens_included(self):
        tracker = CostTracker(ceiling_usd=0.0001)
        # 1M tokens estimated at input price well exceeds $0.0001
        assert tracker.would_exceed_ceiling(1_000_000, "gpt-4o")


class TestSummary:
    def test_summary_keys_present(self):
        tracker = CostTracker()
        tracker.record(_mock_usage(prompt=100, completion=50), "gpt-4o")
        s = tracker.summary()
        assert set(s.keys()) >= {
            "calls",
            "prompt_tokens",
            "completion_tokens",
            "cached_tokens",
            "total_cost_usd",
            "ceiling_usd",
        }

    def test_summary_calls_count(self):
        tracker = CostTracker()
        tracker.record(_mock_usage(), "gpt-4o")
        tracker.record(_mock_usage(), "gpt-4o-mini")
        assert tracker.summary()["calls"] == 2


class TestEstimateCost:
    def test_estimate_positive(self):
        cost = CostTracker.estimate_cost(10_000, "gpt-4o")
        assert cost > 0

    def test_estimate_zero_tokens(self):
        assert CostTracker.estimate_cost(0, "gpt-4o") == pytest.approx(0.0)
