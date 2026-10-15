## Summary

Clears the gpt-4o names left in code after the GPT-6 switch (packet 34 backlog item).

- New `analysis.llm.DEFAULT_MODEL` (`gpt-6-sol`) and `DEFAULT_MODEL_CHEAP` (`gpt-6-luna`), used
  by all ten fallbacks that named gpt-4o / gpt-4o-mini (they apply only when a `config.yaml` key
  is missing). A test keeps the constants equal to the config.
- `doc_summary.py` comments now state Luna's 1.05M-token context and that the tokenizer is an
  approximation for GPT-6.
- `CostTracker.would_exceed_ceiling` no longer names gpt-4o as its default model.
- No behaviour change with the current config.

## Spec

- Spec: `docs/specs/35-model-defaults.md`
- Test plan: `docs/test-plans/35-model-defaults.md`
- PR draft path: `.ai/pr-description/35-model-defaults.md`

## Acceptance Criteria

- [x] AC1: Code defaults equal the configured models.
- [x] AC2: No gpt-4o string literals in source outside the cost table.
- [x] AC3: Analyzers without model config use the code defaults.
- [x] AC4: Lazily-importing modules still don't load the OpenAI library at import.

## Security Review

- [x] Security considerations reviewed in `docs/specs/35-model-defaults.md` (no meaningful
  impact: default names and comments only)

## Validation

- Tests written first: the new test module failed to import before the constants existed
- `make lint`: pass
- `make security`: pass
- `make test`: 1044 passed

Reviewer pass (fresh subagent): no blockers or Important items; suggestions applied (scan covers
`execution/`, precise pricing comment, test placement).

## Risks and follow-ups

- None for the current config. The 100k summary source cap could be raised for Luna in a
  separate change.
