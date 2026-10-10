# Test Plan: openrouter-provider

Path: `docs/test-plans/09-openrouter-provider.md`

## What changed

- Added `analysis/llm.py`: provider settings, client factory, model-ID resolution and JSON-mode
  request extras for OpenAI and OpenRouter.
- `OpenAIClient`, the LP letter, weekly commentary and JARVIS chat build clients via the factory.
- `CostTracker` prefers OpenRouter's reported cost and accepts `openai/`-prefixed model IDs.

## Acceptance criteria coverage

- AC1: `tests/test_llm.py::TestProviderSettings::test_defaults_to_openai_when_block_absent`,
  `test_unknown_provider_raises`; `TestMakeClient::test_openai_client_uses_default_base_url`;
  `TestResolveModel::test_openai_leaves_model_unchanged`;
  `tests/test_api_client.py::TestOpenAIDefault::test_no_extra_body_for_openai`. All existing
  `test_api_client.py` tests pass unchanged with no config.

- AC2: `tests/test_llm.py::TestProviderSettings::test_openrouter_defaults`,
  `test_overrides_applied`; `TestApiKey::test_reads_provider_env_var`,
  `test_missing_key_returns_empty`; `TestMakeClient::test_openrouter_client_base_url_and_header`,
  `test_key_read_from_env_when_not_passed`;
  `tests/test_api_client.py::TestOpenRouterProvider::test_points_at_openrouter`.

- AC3: `tests/test_llm.py::TestResolveModel::test_openrouter_prefixes_bare_model`,
  `test_openrouter_keeps_namespaced_model`;
  `tests/test_api_client.py::TestOpenRouterProvider::test_model_namespaced`,
  `test_estimate_tokens_strips_namespace`.

- AC4: `tests/test_llm.py::TestJsonModeExtraBody::*`;
  `tests/test_api_client.py::TestOpenRouterProvider::test_json_mode_requires_parameter_support`,
  `test_no_extra_body_without_json_mode`.

- AC5: `tests/test_cost_tracker.py::TestOpenRouterCost::test_namespaced_model_uses_openai_pricing`,
  `test_reported_cost_overrides_table`, `test_byok_adds_upstream_cost`,
  `test_byok_upstream_cost_from_real_sdk_usage` (real `CompletionUsage`, where the SDK keeps
  `cost_details` as a dict), `test_reported_cost_counts_toward_ceiling`,
  `test_estimate_cost_namespaced`;
  `tests/test_api_client.py::TestOpenRouterProvider::test_records_reported_cost`.

- AC6: Import check of `dashboard.jarvis`, `dashboard.page_letter`, `dashboard.page_portfolio`,
  `reporting.lp_letter`, `reporting.commentary`, `run_analysis`. Manual smoke test with
  `provider: openrouter`:
  `python run_analysis.py --no-cache --ticker <T>`, `python run_reporting.py`, and one JARVIS chat
  turn in the dashboard. Confirm the responses, and that the logged cost matches the OpenRouter
  activity page.

## Commands

```bash
make lint
. .venv/bin/activate && pytest tests/test_llm.py tests/test_api_client.py tests/test_cost_tracker.py -q
make test
```
