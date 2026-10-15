# Model Defaults in Code

**Spec file:** `docs/specs/35-model-defaults.md`
**Spec slug:** model-defaults
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-15

## Problem statement
Packets 29 and 34 moved every configured LLM use to GPT-6, but the code still names gpt-4o and
gpt-4o-mini as fallbacks in ten places (used only when a `config.yaml` key is missing), and two
comments describe gpt-4o-mini limits. A config without those keys would silently run on the old
models, and the scattered literals will drift again at the next model change. Backlog item from
the packet 34 Reviewer pass.

## Scope
In scope:
- `analysis.llm.DEFAULT_MODEL` (`gpt-6-sol`) and `DEFAULT_MODEL_CHEAP` (`gpt-6-luna`), used by
  every fallback: `run_analysis.py`, the four analyzers, `analysis/sector_analysis.py`,
  `analysis/doc_summary.py`, `reporting/commentary.py`, `reporting/lp_letter.py`,
  `dashboard/jarvis.py`.
- `analysis/doc_summary.py` comments (context size, tokenizer).
- `CostTracker.would_exceed_ceiling`: its default `model` argument no longer names gpt-4o.

Out of scope / non-goals:
- `MAX_SOURCE_TOKENS` (100k): Luna allows 1.05M tokens, but raising the cap changes summaries.
- The cost table's gpt-4o rows and the gpt-4o price used for unknown models (dearer than Sol
  and Luna, so for those tiers the cost ceiling errs high).
- Tests and historical docs that use gpt-4o as sample data.

## Assumptions
- Luna's and Sol's context is 1,050,000 tokens (OpenRouter model list, 2026-10-15).
- Every caller of `would_exceed_ceiling` passes a model (only `api_client.py`), so its default
  matters only for direct calls.

## Proposed behavior / API
### Public interface
- New constants `analysis.llm.DEFAULT_MODEL`, `analysis.llm.DEFAULT_MODEL_CHEAP`.
- `CostTracker.would_exceed_ceiling(estimated_tokens=0, model=None)`: `None` uses the
  unknown-model price (gpt-4o), as the old default did.

### Inputs / outputs
- With the current `config.yaml`, nothing changes: every key is set.
- With a key missing, the fallback is the GPT-6 default instead of gpt-4o.

## Acceptance criteria
- AC1: `DEFAULT_MODEL` and `DEFAULT_MODEL_CHEAP` equal `config.yaml`'s `analysis.openai_model`
  and `analysis.openai_model_cheap`.
- AC2: No source file outside the tests contains a `"gpt-4o..."` string literal, except the cost
  table's gpt-4o rows and the unknown-model price line.
- AC3: With no model keys in the config, earnings and risk use `DEFAULT_MODEL`, filing and
  insider use `DEFAULT_MODEL_CHEAP`.
- AC4: `analysis.cost_tracker` and the modules that import `analysis.llm` lazily
  (`reporting/commentary.py`, `reporting/lp_letter.py`, `dashboard/jarvis.py`) still do not
  import the OpenAI library at module load.

## Security considerations
- No meaningful security impact: default model names and comments only; same provider, keys and
  data.

## Edge cases
- A config that sets only `analyzer_models`: unchanged behaviour (those keys win).
- `would_exceed_ceiling()` with no model: priced as before (gpt-4o rates).

## Test guidance
- AC1 -> `tests/test_model_defaults.py::TestDefaultsMatchConfig`
- AC2 -> `tests/test_model_defaults.py::TestNoStaleLiterals`
- AC3 -> `tests/test_analyzer_cache_model.py::test_no_model_config_uses_code_defaults`
- AC4 -> `tests/test_model_defaults.py::TestLazyImports`
- Edge case (ceiling default) -> `tests/test_model_defaults.py::TestCeilingDefault`

## Decision log
- 2026-10-15: Named constants in `analysis.llm` rather than requiring the config keys: a missing
  key keeps working, and a test ties the constants to `config.yaml` so they move together.
- 2026-10-15: The three modules that import `analysis.llm` inside functions keep doing so; the
  constant joins those local imports instead of adding a top-level import.
- 2026-10-15: `cost_tracker` does not import `analysis.llm` just for a default argument; `None`
  now means the unknown-model price, which is what the old `"gpt-4o"` default produced.
- 2026-10-15 (Reviewer pass): no blockers or Important items. Scan extended to `execution/`;
  pricing comment made precise (gpt-4o pricing errs high only against Sol/Luna-priced models).
