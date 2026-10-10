# Test Plan: llm-review-findings

Path: `docs/test-plans/16-llm-review-findings.md`

## What changed
- `analysis/api_client.py`: retries only temporary errors (408, 409, 429, >= 500).
- `analysis/llm.py`: provider values normalised; config overrides scoped to their own provider;
  error names its source.
- `analysis/cost_tracker.py`: upstream cost is ignored when `is_byok` is explicitly `False`.
- `dashboard/jarvis.py`: streaming moved into a guarded `_stream_reply` helper.

## Acceptance criteria coverage
- AC1: `tests/test_api_client.py::TestRetryPolicy::test_permanent_errors_not_retried`
  [400, 401, 402, 403, 404, 422] and `test_transient_errors_retried` [408, 409, 429, 500, 502,
  503]. Existing `TestRetry` rate-limit tests pass unchanged.
- AC2: `tests/test_llm.py::TestOverrideScoping` (5 tests).
- AC3: `tests/test_llm.py::TestProviderValue` (6 test functions) and the updated
  `TestProviderSettings::test_unknown_env_provider_raises`.
- AC4: `tests/test_llm_call_sites.py::TestJarvisStream` (3 tests). `render_chat` measured at
  50 lines.
- AC5: `tests/test_cost_tracker.py::TestByokRule` (3 tests).
- AC6: `tests/test_llm_call_sites.py::TestReportingCallSites` (2 tests).
- AC7: All of the above, plus `tests/test_api_client.py::TestOpenRouterEndToEnd`.

## Edge cases
- From spec:
  - Config `base_url` with no provider belongs to `openai`:
    `TestOverrideScoping::test_env_openrouter_ignores_openai_overrides`
  - 409 conflict retried: `test_transient_errors_retried[409]`
  - Usage without `is_byok` keeps the old rule: existing `TestOpenRouterCost` tests
- Additional adversarial cases:
  - The key and the host are asserted together, so a key sent to the wrong host is caught:
    `test_openai_key_not_sent_to_openrouter`
  - `None` delta content between real deltas: `TestJarvisStream::test_none_delta_content_is_skipped`
  - A permanent error must not sleep at all: `time.sleep` is patched to assert it is never called
  - `RateLimitError` built around a mock response (no real status) is still retried, by type:
    `tests/test_api_client.py::TestRetry::test_retries_on_rate_limit`
  - Invalid committed config raises even under an env override:
    `TestProviderValue::test_invalid_config_raises_even_under_env_override`
  - Blank or non-string config values: `test_blank_config_value_defaults_to_openai`,
    `test_non_string_config_value_raises_value_error`
  - Live: an unknown model via OpenRouter raises HTTP 400 in 0.2 s with no retry; a live JARVIS
    stream returns text through `_stream_reply`

## Notes
- Flaky risks: none in the suite (all mocked). Live checks are manual.
- Determinism considerations: retry tests patch `time.sleep`.

## Commands
```bash
make lint
make security
make test
LLM_PROVIDER=OpenRouter pytest -q
```
