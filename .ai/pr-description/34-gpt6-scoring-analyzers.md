## Summary

Moves the four scoring analyzers to GPT-6 (stage 3 of the plan), after a shadow test on the
fixed inputs from packets 30, 31 and 33.

- `analysis.analyzer_models`: earnings and risk `gpt-6-sol`, filing and insider `gpt-6-luna`.
- The packet 29 pinning test now guards these models; the reasoning/price check covers every
  configured model.
- README and two living docs name the new models.

## Spec

- Spec: `docs/specs/34-gpt6-scoring-analyzers.md`
- Test plan: `docs/test-plans/34-gpt6-scoring-analyzers.md`
- PR draft path: `.ai/pr-description/34-gpt6-scoring-analyzers.md`

## Acceptance Criteria

- [x] AC1: Analyzer models set to GPT-6 and pinned by a test.
- [x] AC2: Every configured model is a priced reasoning model.
- [x] AC3: Cache lookups use the configured model, so old results are not reused.
- [x] AC4: Living docs name the new models; no config comment names gpt-4o.

## Security Review

- [x] Security considerations reviewed in `docs/specs/34-gpt6-scoring-analyzers.md` (no
  meaningful impact: model names only)

## Validation

- Tests written first: both updated tests failed against the old config
- `make lint`: pass
- `make security`: pass
- `make test`: 1035 passed

Reviewer pass (fresh subagent): no blockers or Important items; nothing in the analyzer path
assumes gpt-4o behaviour. Fixed a stale `config.yaml` example; stale gpt-4o fallback defaults in
code (used only when a config key is missing) logged in the backlog.

Shadow test (50 seeded-random candidates, production analyzers, both model sets fresh, no DB
writes, $5.96): no errors.

| Analyzer | Std dev current / GPT-6 | Rank agreement | Mean change |
|---|---|---|---|
| Earnings (4o -> Sol) | 0.79 / 1.05 | 0.70 | -0.98 |
| Filing (4o-mini -> Luna) | 1.06 / 1.03 | 0.70 | -0.57 |
| Risk (4o -> Sol) | 0.50 / 0.30 | 0.08 | +0.08 |
| Insider (4o-mini -> Luna) | 2.51 / 1.69 | 0.50 | +1.66 |

Insider: gpt-4o-mini rated 21 of 31 STRONG_SELL; Luna none, separating routine from meaningful
selling as the prompt asks.

## Trading impact (measured on the sample)

AI composite mean 6.31 -> 6.14, standard deviation 0.90 -> 0.66, rank correlation 0.77. With the
50 swapped into the 2026-10-14 universe, 12 of 503 tickers change direction, none LONG <-> SHORT.
The full switch (about 200 candidates) will change several times as many (estimate).

## Risks and follow-ups

- The first nightly run after merge recomputes every analysis: about twice as long as usual and
  roughly $12-13 (ceiling $25).
- Risk scores agree poorly between models and Sol's are narrower, so risk counts for less in the
  composite.
