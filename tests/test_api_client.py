"""Tests for analysis/api_client.py."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest
import openai

sys.path.insert(0, str(Path(__file__).parent.parent))

from analysis.api_client import OpenAIClient
from analysis.cost_tracker import CostTracker, CostCeilingExceeded


def _mock_response(content: dict):
    """Build a minimal mock openai ChatCompletion response."""
    resp = MagicMock()
    resp.choices[0].message.content = json.dumps(content)
    usage = MagicMock()
    usage.prompt_tokens = 100
    usage.completion_tokens = 50
    details = MagicMock()
    details.cached_tokens = 0
    usage.prompt_tokens_details = details
    resp.usage = usage
    return resp


def _make_client(ceiling=25.0, model="gpt-4o-mini"):
    tracker = CostTracker(ceiling_usd=ceiling)
    client  = OpenAIClient(api_key="sk-test", model=model, cost_tracker=tracker)
    return client, tracker


class TestChatSuccess:
    def test_returns_parsed_dict(self):
        client, _ = _make_client()
        expected = {"score": 7, "reasoning": "good"}
        with patch.object(client._client.chat.completions, "create",
                          return_value=_mock_response(expected)):
            result = client.chat("sys", "user")
        assert result == expected

    def test_json_mode_set_by_default(self):
        client, _ = _make_client()
        with patch.object(client._client.chat.completions, "create",
                          return_value=_mock_response({"ok": True})) as mock_create:
            client.chat("sys", "user")
        kwargs = mock_create.call_args.kwargs
        assert kwargs.get("response_format") == {"type": "json_object"}

    def test_json_mode_disabled(self):
        client, _ = _make_client()
        with patch.object(client._client.chat.completions, "create",
                          return_value=_mock_response({"ok": True})) as mock_create:
            client.chat("sys", "user", json_mode=False)
        kwargs = mock_create.call_args.kwargs
        assert "response_format" not in kwargs

    def test_model_override_used(self):
        client, _ = _make_client(model="gpt-4o")
        with patch.object(client._client.chat.completions, "create",
                          return_value=_mock_response({"ok": True})) as mock_create:
            client.chat("sys", "user", model="gpt-4o-mini")
        assert mock_create.call_args.kwargs["model"] == "gpt-4o-mini"

    def test_records_cost_after_call(self):
        client, tracker = _make_client()
        with patch.object(client._client.chat.completions, "create",
                          return_value=_mock_response({"ok": True})):
            client.chat("sys", "user")
        assert tracker.total_cost_usd() > 0


class TestRetry:
    def test_retries_on_rate_limit(self):
        client, _ = _make_client()
        good = _mock_response({"ok": True})
        side_effects = [openai.RateLimitError("rate limit", response=MagicMock(), body={}),
                        good]
        with patch.object(client._client.chat.completions, "create",
                          side_effect=side_effects):
            with patch("analysis.api_client.time.sleep"):
                result = client.chat("sys", "user")
        assert result == {"ok": True}

    def test_raises_after_all_retries_exhausted(self):
        client, _ = _make_client()
        err = openai.RateLimitError("rate limit", response=MagicMock(), body={})
        with patch.object(client._client.chat.completions, "create",
                          side_effect=err):
            with patch("analysis.api_client.time.sleep"):
                with pytest.raises(openai.RateLimitError):
                    client.chat("sys", "user")

    def test_sleeps_between_retries(self):
        client, _ = _make_client()
        good = _mock_response({"ok": True})
        err  = openai.RateLimitError("rate limit", response=MagicMock(), body={})
        with patch.object(client._client.chat.completions, "create",
                          side_effect=[err, good]):
            with patch("analysis.api_client.time.sleep") as mock_sleep:
                client.chat("sys", "user")
        mock_sleep.assert_called_once()


class TestCostGuard:
    def test_raises_when_ceiling_reached(self):
        client, tracker = _make_client(ceiling=0.000001)
        # Manually push over the ceiling
        tracker.record(
            MagicMock(prompt_tokens=1_000_000, completion_tokens=0,
                      prompt_tokens_details=MagicMock(cached_tokens=0)),
            "gpt-4o",
        )
        with pytest.raises(CostCeilingExceeded):
            client.chat("sys", "user")


class TestEstimateTokens:
    def test_returns_positive_integer(self):
        client, _ = _make_client()
        n = client.estimate_tokens("You are an analyst.", "Analyse AAPL earnings.")
        assert isinstance(n, int)
        assert n > 0

    def test_longer_prompt_gives_more_tokens(self):
        client, _ = _make_client()
        short = client.estimate_tokens("short", "short")
        long  = client.estimate_tokens("a" * 1000, "b" * 1000)
        assert long > short
