"""Tests for analysis/llm.py."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from analysis.llm import (
    api_key_env,
    get_api_key,
    json_mode_extra_body,
    make_client,
    provider_settings,
    resolve_model,
)

_OPENROUTER = {"llm": {"provider": "openrouter", "app_name": "Meridian"}}


class TestProviderSettings:
    def test_defaults_to_openai_when_block_absent(self):
        s = provider_settings({})
        assert s["provider"] == "openai"
        assert s["base_url"] is None
        assert s["api_key_env"] == "OPENAI_API_KEY"

    def test_openrouter_defaults(self):
        s = provider_settings(_OPENROUTER)
        assert s["base_url"] == "https://openrouter.ai/api/v1"
        assert s["api_key_env"] == "OPENROUTER_API_KEY"

    def test_overrides_applied(self):
        cfg = {
            "llm": {"provider": "openrouter", "api_key_env": "MY_KEY", "base_url": "http://x/v1"}
        }
        s = provider_settings(cfg)
        assert s["api_key_env"] == "MY_KEY"
        assert s["base_url"] == "http://x/v1"

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown llm.provider"):
            provider_settings({"llm": {"provider": "bogus"}})


class TestApiKey:
    def test_reads_provider_env_var(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        assert api_key_env(_OPENROUTER) == "OPENROUTER_API_KEY"
        assert get_api_key(_OPENROUTER) == "sk-or-test"

    def test_missing_key_returns_empty(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        assert get_api_key(_OPENROUTER) == ""


class TestMakeClient:
    def test_openai_client_uses_default_base_url(self):
        client = make_client({}, api_key="sk-test")
        assert "api.openai.com" in str(client.base_url)

    def test_openrouter_client_base_url_and_header(self):
        client = make_client(_OPENROUTER, api_key="sk-or-test")
        assert str(client.base_url).startswith("https://openrouter.ai/api/v1")
        assert client.default_headers.get("X-Title") == "Meridian"

    def test_key_read_from_env_when_not_passed(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-env")
        client = make_client(_OPENROUTER)
        assert client.api_key == "sk-or-env"


class TestResolveModel:
    def test_openai_leaves_model_unchanged(self):
        assert resolve_model({}, "gpt-4o") == "gpt-4o"

    def test_openrouter_prefixes_bare_model(self):
        assert resolve_model(_OPENROUTER, "gpt-4o-mini") == "openai/gpt-4o-mini"

    def test_openrouter_keeps_namespaced_model(self):
        assert resolve_model(_OPENROUTER, "anthropic/some-model") == "anthropic/some-model"


class TestJsonModeExtraBody:
    def test_none_for_openai(self):
        assert json_mode_extra_body({}) is None

    def test_require_parameters_for_openrouter(self):
        assert json_mode_extra_body(_OPENROUTER) == {"provider": {"require_parameters": True}}
