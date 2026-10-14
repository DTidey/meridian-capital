# GPT-6 for Non-Trading LLM Uses

**Spec file:** `docs/specs/29-gpt6-non-trading.md`
**Spec slug:** gpt6-non-trading
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-14

## Problem statement
- Stage 2 of the GPT-6 plan agreed on 2026-10-14: move LLM uses that don't affect trading to the
  GPT-6 family now that packet 28 supports reasoning models.
- `analysis.openai_model` (gpt-4o) drives JARVIS, the LP letter, the weekly commentary and the
  nightly sector analysis report; `analysis.openai_model_cheap` (gpt-4o-mini) drives filing
  summaries. The scoring analyzers use `analysis.analyzer_models`, which sets all four explicitly,
  so these two keys don't reach AI scores.

## Scope
In scope:
- `config.yaml`: `openai_model: gpt-6-sol`, `openai_model_cheap: gpt-6-luna`.

Out of scope / non-goals:
- `analysis.analyzer_models` (stage 3, after a shadow test).

## Assumptions
- Checked live on 2026-10-14 via OpenRouter (BYOK): gpt-6-luna summarised AAPL's Q3 2026 8-K in
  7.0 s for $0.00084 (gpt-4o-mini: 3.2 s, $0.00081) with more detail and no unverified figures;
  gpt-6-sol answered a JARVIS question fully through the real streaming path (first text 3.4 s,
  gpt-4o 1.2 s). List prices: Sol 2.00/10.00, Luna 0.10/0.50 per million tokens (gpt-4o
  2.50/10.00, gpt-4o-mini 0.15/0.60).

## Proposed behavior / API
- Configuration only.

## Acceptance criteria
- AC1: `analysis.openai_model` is `gpt-6-sol` and `analysis.openai_model_cheap` is `gpt-6-luna`.
- AC2: `analysis.analyzer_models` is unchanged (earnings/risk gpt-4o, filing/insider gpt-4o-mini).
- AC3: Both new models are treated as reasoning models and have prices in the cost table.

## Security considerations
- No meaningful security impact: same provider and key; model names only.

## Edge cases
- Saved filing summaries keep their original model (shown in their caption); new ones use Luna.
- The dashboard rereads config about hourly, or on restart.
- First text in JARVIS arrives about 2 s later (reasoning).

## Test guidance
- AC1-AC3 -> `tests/test_config_models.py`

## Decision log
- 2026-10-14: Sol (not Astra) for `openai_model`: same tier and slightly cheaper than gpt-4o;
  Astra costs 4x and isn't needed for these uses.
- 2026-10-14: A test pins `analyzer_models` so a config edit can't move AI scores without the
  stage 3 shadow test.
