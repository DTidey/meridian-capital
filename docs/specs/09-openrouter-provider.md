# OpenRouter LLM Provider

**Spec file:** `docs/specs/09-openrouter-provider.md`
**Status:** Done
**Date:** 2026-10-10

## Purpose

All LLM calls (Layer 3 analysers, sector analysis, daily LP letter, weekly commentary, JARVIS chat)
currently go directly to the OpenAI API. Make the provider selectable so the same calls can be
routed through OpenRouter's OpenAI-compatible API. That gives one key for many model vendors and
provider failover, and OpenRouter reports the actual cost of each call.

OpenAI remains the default; switching is a one-line change to `llm.provider` in `config.yaml`
plus setting `OPENROUTER_API_KEY`. The `openai` Python SDK is kept for both providers.

## Acceptance criteria

- AC1: A new `llm` block in `config.yaml` selects the provider (`openai` | `openrouter`). With the
  block absent or set to `openai`, behaviour is unchanged: default OpenAI base URL,
  `OPENAI_API_KEY`, bare model names. An unknown provider raises `ValueError`.

- AC2: With `provider: openrouter`, clients use base URL `https://openrouter.ai/api/v1` and read
  the key from `OPENROUTER_API_KEY`. `base_url` and `api_key_env` can be overridden in config.
  `app_name` is sent as the `X-Title` attribution header.

- AC3: With `provider: openrouter`, bare model names from config (`gpt-4o`) are sent as
  `openai/gpt-4o`. Names already containing `/` are sent unchanged.

- AC4: With `provider: openrouter` and JSON mode on, requests include
  `provider.require_parameters: true` so OpenRouter only routes to upstream providers that honour
  `response_format`.

- AC5: `CostTracker.record` uses the cost OpenRouter reports (`usage.cost` plus
  `usage.cost_details.upstream_inference_cost` for bring-your-own-key requests) when present, and
  falls back to the pricing table otherwise. Pricing lookups accept `openai/`-prefixed IDs. The
  cost ceiling applies to reported costs.

- AC6: `analysis/api_client.py`, `reporting/lp_letter.py`, `reporting/commentary.py` and
  `dashboard/jarvis.py` all build their client through `analysis.llm.make_client`. JARVIS uses
  `analysis.openai_model` from config rather than a hardcoded model. On a missing key,
  `run_analysis.py` logs the configured provider and the letter page names the provider-specific
  key env var. The key variable name is kept out of logs so CodeQL's clear-text-logging check
  doesn't flag it.

## Files changed

| File | Change |
|---|---|
| `analysis/llm.py` | New: provider settings, client factory, model resolution, JSON-mode extras |
| `analysis/api_client.py` | Accept `config`; use factory, model resolution, JSON-mode extras |
| `analysis/cost_tracker.py` | Prefer reported cost; namespaced pricing lookup |
| `run_analysis.py` | Provider-aware API key lookup; pass config to client |
| `reporting/lp_letter.py`, `reporting/commentary.py` | Use factory and resolved model |
| `dashboard/jarvis.py`, `dashboard/page_portfolio.py` | Use factory; model from config |
| `dashboard/page_letter.py` | Provider-aware missing-key hint |
| `config.yaml`, `.env.example`, `README.md`, `CLAUDE.md` | `llm` block and key documentation |

## Security considerations

- Auth/authz impact: None.
- Secrets or credential handling: A new secret, `OPENROUTER_API_KEY`, is read from the environment
  only. It is never logged or written to config or the database. Only the env var's name appears
  in error messages.
- Network or external service impact: With `provider: openrouter`, prompts (which include
  portfolio positions, P&L and filing extracts) are sent to OpenRouter and from there to the
  upstream model provider. OpenRouter does not log prompts by default. The account's privacy
  settings should exclude providers that retain or train on data. The default (`openai`) changes
  nothing.
