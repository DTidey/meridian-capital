# Test Plan: reasoning-model-support

Path: `docs/test-plans/28-reasoning-model-support.md`

## What changed
- `analysis/llm.py`: `is_reasoning_model`, `completion_params`.
- `dashboard/jarvis.py`, `reporting/lp_letter.py`, `reporting/commentary.py`,
  `analysis/doc_summary.py`: output and sampling parameters via `completion_params`.
- `analysis/cache.py`: `get(..., model=)`; the four analyzers pass their model.
- `analysis/cost_tracker.py`: GPT-6 prices (`-pro` variants at the same rates).
- `docs/specification.md`: new optional `llm` settings.

## Acceptance criteria coverage
- AC1: `tests/test_llm.py::TestCompletionParams` (classic, reasoning defaults, config overrides,
  9 prefix cases)
- AC2: `tests/test_llm_call_sites.py::TestReasoningParams` (letter, commentary, JARVIS, summaries;
  both classic and reasoning)
- AC3: `tests/test_analysis_cache.py::TestModelAwareGet` (5 tests);
  `tests/test_analyzer_cache_model.py` (8 tests: each analyzer passes its configured or default
  model; verified to fail when `model=` is removed from one analyzer)
- AC4: `tests/test_cost_tracker.py::TestGpt6Pricing` (6 tests)
- AC5: `TestReasoningParams::test_letter_and_commentary_classic_unchanged`,
  `test_jarvis_classic_unchanged`; existing call-site and doc-summary tests unchanged

## Edge cases
- From spec: stored row without a model (`test_missing_stored_model_is_miss`).
- Additional adversarial cases:
  - "omni-moderation" isn't treated as an o-series reasoning model
  - `openai/` prefix on either side of the cache comparison
  - Config prefixes replace (not extend) the defaults: `test_config_overrides`
  - Plain-string and null config values: `TestReasoningConfigRobustness`
  - Other vendors' `-pro` models not priced as their base: `TestProSuffix`

## Notes
- Production cache check (2026-10-14): every stored analyzer result's model matches the current
  config once the `openai/` prefix is ignored, so tonight's run re-uses all of them.
- Live smoke test via OpenRouter (BYOK): gpt-6-luna with `max_completion_tokens` and
  `reasoning_effort` answered in 3.2 s for $0.00002; gpt-4o-mini still gets `max_tokens` and
  `temperature`.

## Commands
```bash
make lint
make test
```
