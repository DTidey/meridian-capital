# Reasoning Model Support

**Spec file:** `docs/specs/28-reasoning-model-support.md`
**Spec slug:** reasoning-model-support
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-14

## Problem statement
- The user is considering moving from GPT-4o / GPT-4o-mini to the GPT-6 family (GPT-6 Luna, Sol),
  released 2026-09-22. Model names are config (`analysis.analyzer_models`,
  `analysis.openai_model`, `analysis.openai_model_cheap`), but the code isn't ready (stage 1 of
  the plan agreed on 2026-10-14):
  1. GPT-6 models are reasoning models. OpenRouter lists no `temperature` support for them, and
     reasoning tokens count against the output limit. JARVIS (`max_tokens=600`), the LP letter
     (600), weekly commentary (800) and filing summaries (1,500) all pass `temperature` and a
     small `max_tokens`, so replies could be cut short or rejected.
  2. The analysis cache isn't keyed by model, so after a switch the old model's results are
     served until they expire (30 days).
  3. The cost table only prices gpt-4o and gpt-4o-mini; other models are estimated at gpt-4o
     prices.
- No model is switched in this packet.

## Scope
In scope:
- A shared helper for free-text completion parameters, used by JARVIS, the LP letter, the
  commentary and filing summaries.
- Model-aware cache lookups in the four analyzers.
- GPT-6 prices in the cost table.

Out of scope / non-goals:
- Changing any configured model (stages 2 and 3: low-risk uses first, then a shadow test before
  the scoring analyzers).
- JSON-mode analyzer calls' parameters: they pass neither `temperature` nor `max_tokens`.

## Assumptions
- Reasoning models: names (after any `openai/` prefix) starting with `o1`, `o3`, `o4`, `gpt-5` or
  `gpt-6`. OpenRouter lists `max_completion_tokens` and `reasoning_effort` for GPT-6 models and no
  `temperature` (checked 2026-10-14). OpenAI's API rejects `max_tokens` and non-default
  `temperature` for reasoning models.
- GPT-6 prices per million tokens (OpenRouter list, 2026-10-14): Luna 0.10 in / 0.50 out; Sol and
  6.1 Sol 2.00 / 10.00; Astra 10.00 / 50.00. No cached-input discount is assumed.

## Proposed behavior / API
### Public interface
- `analysis.llm.is_reasoning_model(config, model) -> bool`
- `analysis.llm.completion_params(config, model, max_tokens, temperature) -> dict`
- `AnalysisCache.get(analyzer, ticker, artifact_id, model=None)`
- New config keys (all optional): `llm.reasoning_model_prefixes`, `llm.reasoning_effort` (default
  `low`), `llm.reasoning_token_budget` (default 4000).

## Acceptance criteria
- AC1: `completion_params` returns `{"max_tokens": n, "temperature": t}` for other models, and
  `{"max_completion_tokens": n + reasoning_token_budget, "reasoning_effort": effort}` (no
  temperature) for reasoning models. Prefixes, effort and budget come from `llm` config with the
  defaults above; matching ignores an `openai/` prefix.
- AC2: JARVIS streaming, the LP letter, the weekly commentary and filing summaries build their
  request parameters with `completion_params`, keeping their current limits and temperatures for
  non-reasoning models.
- AC3: `AnalysisCache.get(..., model=m)` returns a stored result only if its model equals `m`,
  ignoring an `openai/` prefix on either side; without `model` it behaves as before. Each of the
  four analyzers determines its model before the lookup and passes it.
- AC4: The cost table prices gpt-6-luna, gpt-6-sol, gpt-6.1-sol and gpt-6-astra, and explicitly
  their `-pro` variants at the same rates (OpenRouter list, 2026-10-14). Other `-pro` models are
  not priced as their base model.
- AC5: With the current configuration (gpt-4o / gpt-4o-mini), requests are unchanged.

## Security considerations
- No meaningful security impact: request parameters, cache matching and a price table.

## Edge cases
- A cached result with no stored model: treated as a miss when a model is given.
- Unknown `reasoning_effort` value in config: passed through; the provider validates it.
- Saved filing summaries (dashboard) are shown whatever their model; they display it.

## Test guidance
- AC1 -> `tests/test_llm.py::TestCompletionParams`
- AC2 -> `tests/test_llm_call_sites.py::TestReasoningParams`
- AC3 -> `tests/test_analysis_cache.py::TestModelAwareGet`, `tests/test_analyzer_cache_model.py`
- AC4 -> `tests/test_cost_tracker.py::TestGpt6Pricing`
- AC5 -> existing call-site tests unchanged

## Decision log
- 2026-10-14: Detection by configurable name prefixes, not a hard-coded list of models, so new
  reasoning families need only a config change.
- 2026-10-14: Reasoning budget added on top of the visible-text limit rather than replacing it,
  so replies aren't truncated when the model reasons first.
- 2026-10-14: Cache mismatch is a miss (then overwritten) rather than a cache key change, so no
  schema or key migration is needed.
- 2026-10-14 (Reviewer pass): no blockers. Important: nothing tested that the analyzers pass
  their model to the cache; added `tests/test_analyzer_cache_model.py` (fails if `model=` is
  dropped). Also: a plain-string or null prefix setting no longer misbehaves; `-pro` pricing is
  explicit for GPT-6 only; OpenRouter aliases documented as needing their own prefix. For stage 3:
  OpenRouter lists `response_format` for GPT-6, so JSON-mode routing with `require_parameters`
  works; analyzers would use the provider's default reasoning effort.
