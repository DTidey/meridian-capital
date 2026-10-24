# Short Book Conviction Tilt

**Spec file:** `docs/specs/48-short-conviction-tilt.md`
**Spec slug:** short-conviction-tilt
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-24

## Problem statement
The conviction-tilt optimiser (`portfolio/optimizer.py`), which the nightly run uses, gives
extra weight to its highest-conviction positions: the top 5% of each book get
`top5_multiplier` (1.5x) and the next 5% get `top10_multiplier` (1.25x).
`_apply_conviction_tilt` sorts each book by `combined_score` descending before applying the
multipliers. That is right for longs (high score = strong long) but backwards for shorts: a low
score is a strong short. With 20 shorts, the 1.5x and 1.25x multipliers go to the two shorts
with the highest scores, the weakest in the book.

The candidate selection in the same function already sorts shorts ascending (strongest first),
so only the tilt is wrong. Raised in `docs/backlog.md` while scoping packet 43.

Goal: the tilt rewards the strongest shorts (lowest scores), as it does the strongest longs.

## Scope
In scope:
- `portfolio/optimizer.py` `_apply_conviction_tilt`: sort direction per book; the short book
  is sorted ascending.
- `docs/specs/04-portfolio-construction.md` section 5.6 step 3: say which end is strongest.
- `docs/backlog.md`: remove the item.
- `tests/test_optimizer.py`: tilt tests.

Out of scope / non-goals:
- Multiplier values, the 5%/10% cut-offs, and every other optimiser step.
- The MVO optimiser's own weights (it calls the conviction optimiser only for its cost proxy and
  fallback, which this fix covers).

## Assumptions
- Lower `combined_score` means a stronger short everywhere in Layer 4: selection takes the
  lowest scores (`portfolio/optimizer.py`), MVO maps a lower score to a lower expected return,
  and rebalance measures conviction as distance from 50.

## Proposed behavior / API
### Public interface
- `portfolio.optimizer._apply_conviction_tilt(book, top5_mult, top10_mult, gross_target,
  ascending=False)`: `ascending=True` ranks the lowest score first. `optimise` passes
  `ascending=True` for the short book. No change to `optimise`'s signature or output columns.

### Inputs / outputs
- Output: the same book with tilted weights summing to `gross_target`; in the short book the
  lowest scores get the multipliers.
- Error handling: unchanged (an empty book is returned as is).

## Acceptance criteria
- AC1: In the short book, the lowest `combined_score` gets the `top5_multiplier` and the next
  ones the `top10_multiplier`; the highest-scoring shorts get the base weight.
- AC2: The long book is unchanged: the highest `combined_score` gets the `top5_multiplier`.
- AC3: Each book's tilted weights still sum to its gross target, and `optimise` applies the
  short-book ordering (the strongest short ends with at least the weight of the weakest when no
  later constraint binds).

## Security considerations
- No meaningful security impact: in-memory reordering of weights; no new inputs, secrets,
  network or file access. It changes paper-book sizing only; nightly runs are `--whatif`.

## Edge cases
- One short: it gets the top-5% multiplier either way; weights still sum to the gross target.
- Ties at the cut-off: pandas sort order decides, as for longs today.
- Empty short book: returned unchanged.

## Test guidance
- AC1 -> `TestConvictionTilt::test_strongest_short_gets_top_multiplier`,
  `test_weakest_short_gets_base_weight`
- AC2 -> `TestConvictionTilt::test_strongest_long_gets_top_multiplier`
- AC3 -> `TestConvictionTilt::test_tilted_books_sum_to_gross`,
  `test_optimise_tilts_strongest_short`

## Decision log
- 2026-10-24: An `ascending` flag, matching the selection sort a few lines above, rather than
  ranking by distance from 50. Both give the same order on packet 43's bands; the flag keeps
  the tilt's meaning tied to the book, not to the score scale.
- 2026-10-24: Own packet, separate from packet 43 (owner's call).
