# OpenRouter LLM Provider

**Spec file:** `docs/specs/09-openrouter-provider.md`
**Spec slug:** openrouter-provider
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-10

## Problem statement
- Every LLM call (Layer 3 analysers, sector analysis, daily LP letter, weekly commentary, JARVIS
  chat) went directly to the OpenAI API, with the client built separately in four places.
- The user wants the option of routing the same calls through OpenRouter. That gives one key for
  many model vendors, provider failover, and the actual cost of each call reported back.

## Scope
In scope:
- A config-selected provider (`openai` or `openrouter`), with OpenAI remaining the default.
- One client factory used by every LLM call site.
- Model-ID namespacing for OpenRouter, and restricting JSON-mode requests to providers that
  honour `response_format`.
- Cost tracking that prefers the cost reported by OpenRouter.

Out of scope / non-goals:
- Changing the default provider to OpenRouter.
- Evaluating or adopting non-OpenAI models.
- Keying the analysis cache on model.
- Renaming `OpenAIClient`, or moving to the Responses API, tools or embeddings.

## Assumptions
- OpenRouter's chat completions API is OpenAI-compatible, including `response_format` and
  streaming (OpenRouter docs, checked 2026-10-10).
- OpenRouter always returns `usage.cost`, plus `usage.cost_details.upstream_inference_cost` on
  bring-your-own-key (BYOK) requests. Both are in USD-denominated credits.
- The `openai` SDK keeps unknown response fields, with nested ones as plain dicts.

## Proposed behavior / API
### Public interface
- `analysis/llm.py` (new): `provider_settings`, `api_key_env`, `get_api_key`, `make_client`,
  `resolve_model`, `json_mode_extra_body`.
- `analysis.api_client.OpenAIClient(api_key, model, cost_tracker, config=None)`.
- `analysis.cost_tracker.CostTracker.record` (reported-cost path), and pricing lookups that accept
  `openai/`-prefixed IDs.
- `config.yaml`: new `llm` block with `provider`, `base_url`, `api_key_env` and `app_name`.
- CLI entry points: none new. `run_analysis.py` and `run_reporting.py` pick up the provider from
  config.

### Inputs / outputs
- Inputs: the config dict, plus `OPENAI_API_KEY` or `OPENROUTER_API_KEY` in the environment.
- Outputs: an `openai.OpenAI` client pointed at the configured provider, and per-call cost records.
- Error handling:
  - An unknown provider raises `ValueError("Unknown llm.provider ...")`.
  - `make_client` without an `api_key` raises `KeyError` when the provider's key variable is
    unset.
  - `run_analysis.py` logs `API key for llm.provider=<p> not set` and exits 1.

### Examples
```python
cfg = {"llm": {"provider": "openrouter"}}
client = make_client(cfg)                  # base_url https://openrouter.ai/api/v1
resolve_model(cfg, "gpt-4o")               # "openai/gpt-4o"
json_mode_extra_body(cfg)                  # {"provider": {"require_parameters": True}}
```

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
- Input handling or injection risk: None new. Prompts are built exactly as before.
- Secrets or credential handling: A new secret, `OPENROUTER_API_KEY`, is read from the environment
  only. It is never logged or written to config or the database. The missing-key log line names the
  provider, not the variable.
- Data exposure or privacy impact: With `openrouter`, prompts containing positions, P&L and filing
  extracts go to OpenRouter and then the upstream provider. OpenRouter doesn't log prompts by
  default. Account privacy settings should exclude providers that retain or train on data.
- File system access impact: None.
- Network or external service impact: A new outbound host, `openrouter.ai`, used only when the
  provider is `openrouter`. The default is unchanged.
- Dependency or supply-chain impact: None. The existing `openai` SDK is reused.
- Security notes for reviewers/testers: Confirm no code path logs the key, and that the default
  config still calls OpenAI directly.

## Edge cases
- The `llm` block is absent: OpenAI defaults apply.
- A model ID already containing `/` is sent unchanged.
- `usage.cost` is absent (direct OpenAI): the pricing table is used.
- `usage.cost_details` arrives as a dict, an object or `None`.
- `usage.cost` is not a number (for example a test mock): the pricing table is used.
- `json_mode=False`: no `extra_body` is sent.
- An unknown model gets the gpt-4o pricing fallback.

## Test guidance
- AC1 -> `tests/test_llm.py::TestProviderSettings::test_defaults_to_openai_when_block_absent`,
  `test_unknown_provider_raises`; `tests/test_api_client.py::TestOpenAIDefault`
- AC2 -> `tests/test_llm.py::TestProviderSettings::test_openrouter_defaults`, `test_overrides_applied`;
  `TestApiKey`; `TestMakeClient`
- AC3 -> `tests/test_llm.py::TestResolveModel`; `tests/test_api_client.py::TestOpenRouterProvider::test_model_namespaced`
- AC4 -> `tests/test_llm.py::TestJsonModeExtraBody`;
  `tests/test_api_client.py::TestOpenRouterProvider::test_json_mode_requires_parameter_support`
- AC5 -> `tests/test_cost_tracker.py::TestOpenRouterCost`;
  `tests/test_api_client.py::TestOpenRouterProvider::test_records_reported_cost`
- AC6 -> import check of the changed modules, plus the live smoke test in the test plan

## Decision log
- 2026-10-10: Kept the `openai` SDK and swapped `base_url` instead of adding an OpenRouter SDK.
  The API is compatible, and the retry logic built on the SDK's exceptions keeps working.
- 2026-10-10: Bare model names are auto-prefixed `openai/`, so switching provider needs no model
  renames.
- 2026-10-10: Cost = `cost` + `upstream_inference_cost`, so the ceiling holds under both credits
  and BYOK. `cost_details` handles dict form, after a mock-only test missed it.
- 2026-10-10: `require_parameters` is sent for JSON mode only. Free-text calls don't need it.
- 2026-10-10: The missing-key log names the provider, not the key variable. CodeQL flagged the
  variable name as possible secret logging.
