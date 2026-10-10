# LLM Review Findings

**Spec file:** `docs/specs/16-llm-review-findings.md`
**Spec slug:** llm-review-findings
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-10

## Problem statement
- The 2026-10-10 Reviewer pass on packets 09-12 found no blockers but five Important issues in
  the LLM layer. Test plans 09 and 12 recorded the missing adversarial tests as "to be added".
- The issues:
  1. `analysis/api_client.py` retries every `APIStatusError`, including permanent errors such
     as 400 (bad model ID), 402 (out of credits) and 404 (no provider supports the request).
     Each such call wastes about 62 s of back-off before failing.
  2. `analysis/llm.py` applies config `base_url` and `api_key_env` overrides whichever provider
     `LLM_PROVIDER` selects, so one provider's key can be sent to the other's host.
  3. `dashboard/jarvis.py` reads `chunk.choices[0]` without a guard. A streamed chunk with
     empty `choices` (allowed by the schema; used for trailing usage) would raise and discard a
     complete reply.
  4. Packet 09 AC6 (call sites use the shared factory and configured model) has no automated
     test.
  5. Packet 12 has no adversarial tests. Its value handling is also strict: `OpenRouter` raises,
     and the error message always says `llm.provider` even when the bad value came from
     `LLM_PROVIDER`.

## Scope
In scope:
- Retrying only temporary API errors.
- Scoping config provider overrides to the provider they were written for.
- Case-insensitive provider values, with an error that names its source.
- A guarded, testable JARVIS streaming helper.
- A BYOK cost rule tied to `usage.is_byok`.
- The adversarial tests recorded in test plans 09 and 12, plus AC6 wiring tests.

Out of scope / non-goals:
- Other Reviewer suggestions: the letter page's error path, resolving the provider once per
  client, tracking letter/commentary/JARVIS spend, the long docstring, hash-pinned requirements,
  and enforcing the pip-tools pin.
- Deduplicating `_call_llm` in `reporting/lp_letter.py` and `reporting/commentary.py`.
- Retrying connection errors in the wrapper. The SDK already retries them; this is unchanged.

## Assumptions
- The openai SDK's own retry rule is the reference for "temporary": 408, 409, 429 and >= 500
  (`openai._base_client.BaseClient._should_retry`, SDK 3.26).
- OpenRouter's `usage.is_byok` is a boolean on the usage object (observed on live responses
  2026-10-10). Non-BYOK requests report `upstream_inference_cost` as 0 or null (OpenRouter docs).

## Proposed behavior / API
### Public interface
- `analysis.api_client`: a module-level predicate `_is_retryable(exc) -> bool` replaces the
  `_RETRYABLE` tuple.
- `analysis.llm.provider_settings`: normalises values and scopes overrides. Signature unchanged.
- `analysis.cost_tracker._reported_cost`: honours `is_byok`.
- `dashboard.jarvis._stream_reply(cfg, messages) -> Iterator[str]`: a new helper yielding text
  deltas. `render_chat` consumes it.
- CLI entry points: none.

### Inputs / outputs
- Inputs: unchanged (config dict, env vars, SDK responses).
- Outputs: unchanged on success.
- Error handling:
  - Non-retryable `APIStatusError`s are raised on the first attempt, with no sleep.
  - An unknown provider raises `ValueError` naming `LLM_PROVIDER` or `llm.provider`.

### Examples
```python
provider_settings({})  # with LLM_PROVIDER=" OpenRouter " -> provider "openrouter"
```

## Acceptance criteria
- AC1: `OpenAIClient.chat` retries an `APIStatusError` only for status 408, 409, 429 or >= 500.
  Any other status (for example 400, 401, 402, 404) is raised on the first attempt without
  sleeping. Rate-limit retry behaviour is unchanged.
- AC2: Config `base_url` and `api_key_env` overrides apply only when the effective provider equals
  the config's `llm.provider` (default `openai`). When `LLM_PROVIDER` selects a different
  provider, that provider's defaults are used. `app_name` still applies.
- AC3: Provider values from `LLM_PROVIDER` and `llm.provider` are stripped and lower-cased; a
  blank `llm.provider` means `openai`. An unknown or non-string value raises `ValueError`
  whose message names the source (`LLM_PROVIDER` or `llm.provider`).
- AC4: JARVIS streaming goes through `dashboard.jarvis._stream_reply`. It builds its client with
  `make_client(cfg)` and model `resolve_model(cfg, analysis.openai_model)`, and skips chunks
  with empty `choices`. `render_chat` is no longer over 50 lines.
- AC5: `_reported_cost` adds `upstream_inference_cost` unless `usage.is_byok` is explicitly
  `False`. A reported `cost` of 0 is recorded as 0.0, not table pricing.
- AC6: Tests check that `reporting.lp_letter._call_llm` and `reporting.commentary._call_llm`
  call `make_client(cfg)` and request `resolve_model(cfg, analysis.openai_model)`.
- AC7: The adversarial tests listed in test plans 09 and 12 exist and pass.

## Files changed

| File | Change |
|---|---|
| `analysis/api_client.py` | `_is_retryable` predicate |
| `analysis/llm.py` | Normalised provider values; scoped overrides; source-named error |
| `analysis/cost_tracker.py` | `is_byok`-aware upstream cost |
| `dashboard/jarvis.py` | `_stream_reply` helper with empty-`choices` guard |
| `tests/test_api_client.py`, `tests/test_llm.py`, `tests/test_cost_tracker.py` | New tests |
| `tests/test_llm_call_sites.py` | New: wiring tests for letter, commentary, JARVIS |
| `docs/test-plans/09-*.md`, `docs/test-plans/12-*.md` | Gaps marked as closed |

## Security considerations
- Auth/authz impact: None.
- Input handling or injection risk: Provider values are normalised and checked against an
  allow-list, as before.
- Secrets or credential handling: AC2 stops one provider's API key being sent to the other's host
  when an override and `LLM_PROVIDER` disagree.
- Data exposure or privacy impact: None new. The openai SDK also sends `OPENAI_ORG_ID`,
  `OPENAI_PROJECT_ID` and `OPENAI_CUSTOM_HEADERS` from the environment to OpenRouter-bound
  requests. They're unset on this machine; noted for awareness.
- File system access impact: None.
- Network or external service impact: Fewer wasted requests; permanent errors fail fast.
- Dependency or supply-chain impact: None.
- Security notes for reviewers/testers: Check the AC2 wrong-host test asserts the key and the
  base URL together.

## Edge cases
- The config sets `base_url` but no `provider`: the override belongs to `openai` (the default).
- An invalid `llm.provider` in config raises even when `LLM_PROVIDER` overrides it. A broken
  committed config should fail loudly.
- A 409 conflict is retried, matching the SDK.
- Usage without an `is_byok` field (direct OpenAI, or mocks): upstream is added only if numeric,
  as before.
- A stream whose last chunk carries usage with `choices=[]`: skipped, and the reply is kept.

## Test guidance
- AC1 -> `tests/test_api_client.py::TestRetryPolicy`
- AC2 -> `tests/test_llm.py::TestOverrideScoping`
- AC3 -> `tests/test_llm.py::TestProviderValue`
- AC4 -> `tests/test_llm_call_sites.py::TestJarvisStream`
- AC5 -> `tests/test_cost_tracker.py::TestByokRule`
- AC6 -> `tests/test_llm_call_sites.py::TestReportingCallSites`
- AC7 -> `tests/test_api_client.py::TestOpenRouterEndToEnd`, plus all of the above

## Decision log
- 2026-10-10: Retry set copied from the openai SDK's `_should_retry` rather than invented.
  This also stops pointless retries of 4xx errors on direct OpenAI, a deliberate behaviour change.
- 2026-10-10: Lower-case normalisation instead of strict matching. `OpenRouter` is an obvious
  intent, and the allow-list still rejects anything unknown.
- 2026-10-10: Overrides follow `llm.provider` rather than becoming per-provider blocks; this
  keeps the config shape unchanged.
- 2026-10-10 (Reviewer pass): Added tests for an invalid config under an env override, for the
  key in the reverse wrong-host case, and for blank or non-string config values. Mid-stream
  error chunks and the empty JARVIS bubble on a client error were already present before this
  change and are left as follow-ups.
- 2026-10-10: `upstream_inference_cost` is excluded only when `is_byok` is explicitly `False`.
  Usage without the field keeps today's behaviour.
