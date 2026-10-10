"""LLM provider selection: OpenAI direct, or OpenRouter via its OpenAI-compatible API."""

import os

import openai

_PROVIDERS: dict[str, dict] = {
    "openai": {"base_url": None, "api_key_env": "OPENAI_API_KEY"},
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
    },
}


def _provider_name(value: str, source: str) -> str:
    name = value.strip().lower()
    if name not in _PROVIDERS:
        raise ValueError(f"Unknown {source} {name!r}; expected one of {list(_PROVIDERS)}")
    return name


def provider_settings(config: dict | None) -> dict:
    """Resolve the `llm` config block against per-provider defaults.

    The LLM_PROVIDER env var, if set, overrides `llm.provider` so a machine can switch
    provider via .env without editing the committed config.
    """
    llm_cfg = (config or {}).get("llm") or {}
    config_value = str(llm_cfg.get("provider") or "").strip() or "openai"
    config_provider = _provider_name(config_value, "llm.provider")
    env_value = os.environ.get("LLM_PROVIDER", "")
    provider = _provider_name(env_value, "LLM_PROVIDER") if env_value.strip() else config_provider
    settings = {"provider": provider, **_PROVIDERS[provider]}
    # base_url/api_key_env were written for config_provider; never apply them to another host
    scoped = ("base_url", "api_key_env") if provider == config_provider else ()
    for key in (*scoped, "app_name"):
        if llm_cfg.get(key):
            settings[key] = llm_cfg[key]
    return settings


def api_key_env(config: dict | None) -> str:
    """Name of the environment variable holding the API key for the configured provider."""
    return provider_settings(config)["api_key_env"]


def get_api_key(config: dict | None) -> str:
    return os.environ.get(api_key_env(config), "")


def make_client(config: dict | None, api_key: str | None = None) -> openai.OpenAI:
    """Build an openai.OpenAI client pointed at the configured provider."""
    settings = provider_settings(config)
    headers = None
    if settings["provider"] == "openrouter" and settings.get("app_name"):
        headers = {"X-Title": settings["app_name"]}
    if api_key is None:
        api_key = os.environ[settings["api_key_env"]]
    return openai.OpenAI(api_key=api_key, base_url=settings["base_url"], default_headers=headers)


def resolve_model(config: dict | None, model: str) -> str:
    """Map a bare OpenAI model name to the provider's model ID.

    OpenRouter IDs are namespaced ("openai/gpt-4o"); bare names are assumed to be
    OpenAI models so config can switch provider without renaming every model.
    """
    if provider_settings(config)["provider"] == "openrouter" and "/" not in model:
        return f"openai/{model}"
    return model


def json_mode_extra_body(config: dict | None) -> dict | None:
    """Extra request body for JSON mode.

    On OpenRouter, only route to upstream providers that honour response_format.
    """
    if provider_settings(config)["provider"] == "openrouter":
        return {"provider": {"require_parameters": True}}
    return None
