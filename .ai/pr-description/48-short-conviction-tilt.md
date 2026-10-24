## Summary

The conviction-tilt optimiser (the one the nightly run uses) gave its 1.5x and 1.25x weight
multipliers to the weakest shorts. `_apply_conviction_tilt` sorted every book by
`combined_score` descending, which is right for longs but backwards for shorts, where a low
score is a strong short. Selection a few lines above already sorts shorts ascending.

- `_apply_conviction_tilt` takes `ascending`; `optimise` passes `ascending=True` for shorts.
- Spec 04 section 5.6 step 3 now says strongest first for each book; backlog item removed.
- Found while scoping packet 43.

## Spec

- Spec: `docs/specs/48-short-conviction-tilt.md`
- Test plan: `docs/test-plans/48-short-conviction-tilt.md`
- PR draft path: `.ai/pr-description/48-short-conviction-tilt.md`

## Acceptance Criteria

- [x] AC1: The lowest-scoring short gets the top-5% multiplier, the next the top-10%; the
  weakest shorts get the base weight.
- [x] AC2: The long book is unchanged (highest score gets the top multiplier).
- [x] AC3: Tilted books sum to their gross targets; `optimise` gives the strongest short more
  weight than the weakest when no later constraint binds.

## Security Review

- [x] Security considerations reviewed in `docs/specs/48-short-conviction-tilt.md` (no
  meaningful security impact)

## Validation

- `make lint`: pass
- `make test`: see PR checks
- `make security`: pass
- Reproduced on `main` through `optimise`: weakest selected short 4.3%, strongest 2.9%; after
  the fix the strongest gets the larger weight.

## Risks and follow-ups

- Small sizing change: with 20 shorts, one name moves to 1.5x and one to 1.25x. Nightly runs
  are `--whatif`, so nothing trades.
