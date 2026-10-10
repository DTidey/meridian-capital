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

    def test_env_var_overrides_config(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "openrouter")
        s = provider_settings({"llm": {"provider": "openai"}})
        assert s["provider"] == "openrouter"
        assert s["api_key_env"] == "OPENROUTER_API_KEY"

    def test_env_var_overrides_missing_block(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "openrouter")
        assert provider_settings({})["provider"] == "openrouter"

    def test_blank_env_var_falls_back_to_config(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "  ")
        assert provider_settings(_OPENROUTER)["provider"] == "openrouter"
        assert provider_settings({})["provider"] == "openai"

    def test_unknown_env_provider_raises(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "bogus")
        with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
            provider_settings({})

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
        assert str(client.base_url) == "https://api.openai.com/v1/"

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


class TestOverrideScoping:
    _OR_OVERRIDES = {
        "llm": {
            "provider": "openrouter",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key_env": "MY_OR_KEY",
        }
    }

    def test_env_openai_ignores_openrouter_overrides(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        s = provider_settings(self._OR_OVERRIDES)
        assert s["provider"] == "openai"
        assert s["base_url"] is None
        assert s["api_key_env"] == "OPENAI_API_KEY"

    def test_openai_key_not_sent_to_openrouter(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        monkeypatch.setenv("MY_OR_KEY", "sk-or")
        client = make_client(self._OR_OVERRIDES)
        assert str(client.base_url) == "https://api.openai.com/v1/"
        assert client.api_key == "sk-openai"

    def test_env_openrouter_ignores_openai_overrides(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "openrouter")
        cfg = {"llm": {"base_url": "http://proxy.invalid/v1", "api_key_env": "MY_OPENAI_KEY"}}
        s = provider_settings(cfg)
        assert s["base_url"] == "https://openrouter.ai/api/v1"
        assert s["api_key_env"] == "OPENROUTER_API_KEY"

    def test_overrides_kept_when_provider_matches_env(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "openrouter")
        assert provider_settings(self._OR_OVERRIDES)["api_key_env"] == "MY_OR_KEY"

    def test_app_name_applies_under_env_override(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "openrouter")
        s = provider_settings({"llm": {"provider": "openai", "app_name": "Meridian"}})
        assert s["app_name"] == "Meridian"


class TestProviderValue:
    @pytest.mark.parametrize("value", [" openrouter ", "OpenRouter", "OPENROUTER"])
    def test_env_value_normalised(self, monkeypatch, value):
        monkeypatch.setenv("LLM_PROVIDER", value)
        assert provider_settings({})["provider"] == "openrouter"

    def test_config_value_normalised(self):
        assert provider_settings({"llm": {"provider": " OpenAI "}})["provider"] == "openai"

    def test_invalid_config_raises_even_under_env_override(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        with pytest.raises(ValueError, match="Unknown llm.provider 'bogus'"):
            provider_settings({"llm": {"provider": "bogus"}})

    @pytest.mark.parametrize("value", ["", "   ", None])
    def test_blank_config_value_defaults_to_openai(self, value):
        assert provider_settings({"llm": {"provider": value}})["provider"] == "openai"

    def test_non_string_config_value_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown llm.provider '1'"):
            provider_settings({"llm": {"provider": 1}})

    def test_unknown_config_value_names_config_key(self):
        with pytest.raises(ValueError, match="Unknown llm.provider 'bogus'"):
            provider_settings({"llm": {"provider": "Bogus"}})
