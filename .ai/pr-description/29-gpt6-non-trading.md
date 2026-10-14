## Summary

Stage 2 of the GPT-6 plan: moves the LLM uses that don't affect trading to GPT-6.

- `analysis.openai_model`: gpt-4o to **gpt-6-sol** (JARVIS, LP letter, weekly commentary, nightly
  sector analysis report).
- `analysis.openai_model_cheap`: gpt-4o-mini to **gpt-6-luna** (filing summaries).
- The four scoring analyzers stay on gpt-4o / gpt-4o-mini (`analysis.analyzer_models`); a new
  test pins them until the stage 3 shadow test.

Live checks: Luna summarised AAPL's Q3 earnings release with more detail (product revenue,
operating cash flow) and every figure verified, at about the same cost as gpt-4o-mini; Sol gave a
complete, sharper JARVIS answer through the real streaming path. Both are slower to first text
(reasoning). Sol is slightly cheaper than gpt-4o; Luna is cheaper than gpt-4o-mini.

## Spec

- Spec: `docs/specs/29-gpt6-non-trading.md`
- Test plan: `docs/test-plans/29-gpt6-non-trading.md`
- PR draft path: `.ai/pr-description/29-gpt6-non-trading.md`

## Acceptance Criteria

- [x] AC1: `openai_model` gpt-6-sol, `openai_model_cheap` gpt-6-luna.
- [x] AC2: `analyzer_models` unchanged.
- [x] AC3: Both treated as reasoning models and priced.

## Security Review

- [x] Security considerations reviewed in `docs/specs/29-gpt6-non-trading.md` (no meaningful
  impact)

## Validation

- `make lint`: pass
- `make test`: 911 passed
- Live: Luna summary and Sol JARVIS reply checked against gpt-4o-mini / gpt-4o

## After merge

Restart the dashboard (or wait about an hour) for JARVIS, the letter and summaries to pick up the
new models. The nightly run's sector report uses Sol from tonight.
