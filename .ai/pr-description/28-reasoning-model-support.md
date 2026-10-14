## Summary

Stage 1 of moving to the GPT-6 family: makes the code safe for reasoning models without changing
any configured model.

- **Free-text calls** (JARVIS, LP letter, weekly commentary, filing summaries) build their
  parameters with a new `completion_params` helper. For reasoning models (names starting `o1`,
  `o3`, `o4`, `gpt-5`, `gpt-6`, configurable) it drops `temperature`, uses
  `max_completion_tokens` with 4,000 tokens of reasoning headroom, and sets
  `reasoning_effort: low`. Other models get exactly the current parameters.
- **Analysis cache** is model-aware: a stored result from a different model is a miss, so
  switching a model re-runs the analysis instead of serving the old model's result for up to
  30 days.
- **Cost table** prices GPT-6 Luna, Sol, 6.1 Sol and Astra (and explicitly their `-pro` variants) at list prices.
- New optional config: `llm.reasoning_model_prefixes`, `llm.reasoning_effort`,
  `llm.reasoning_token_budget`.

## Spec

- Spec: `docs/specs/28-reasoning-model-support.md`
- Test plan: `docs/test-plans/28-reasoning-model-support.md`
- PR draft path: `.ai/pr-description/28-reasoning-model-support.md`

## Acceptance Criteria

- [x] AC1: `completion_params` for classic and reasoning models, configurable.
- [x] AC2: The four free-text call sites use it.
- [x] AC3: Cache lookups match on model; the analyzers pass theirs.
- [x] AC4: GPT-6 prices in the cost table.
- [x] AC5: Current configuration's requests unchanged.

## Security Review

- [x] Security considerations reviewed in `docs/specs/28-reasoning-model-support.md` (no
  meaningful impact)

## Validation

- Tests written first: 25 failed on the old code
- `make lint`: pass
- `make security`: pass
- `make test`: 908 passed
- Production cache: all stored results match the configured models, so nothing is re-run
- Live: gpt-6-luna via OpenRouter (BYOK) works with the new parameters ($0.00002 per call)

Reviewer pass (fresh subagent): no blockers. Added analyzer-level tests that the model reaches
the cache lookup, hardened config parsing, and made `-pro` pricing explicit for GPT-6 only.

## After merge

No data run. Stage 2 (moving filing summaries, JARVIS, letter and commentary) is a config change.
