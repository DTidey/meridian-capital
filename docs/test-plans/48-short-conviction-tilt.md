# Test Plan: short-conviction-tilt

Path: `docs/test-plans/48-short-conviction-tilt.md`

## What changed
- `portfolio/optimizer.py`: `_apply_conviction_tilt` takes `ascending`; `optimise` passes
  `ascending=True` for the short book.
- `tests/test_optimizer.py`: new `TestConvictionTilt` (no tilt tests existed).

## Acceptance criteria coverage
- AC1: `TestConvictionTilt::test_strongest_short_gets_top_multiplier`,
  `test_weakest_short_gets_base_weight`
- AC2: `TestConvictionTilt::test_strongest_long_gets_top_multiplier`
- AC3: `TestConvictionTilt::test_tilted_books_sum_to_gross`,
  `test_optimise_tilts_strongest_short`

## Edge cases
- From spec: one short (`test_single_short`); empty book (`test_empty_book_unchanged`).
- Additional adversarial cases:
  - Shuffled input order: the two lowest scores still get the multipliers
    (`test_unsorted_input_short_book`)
  - End to end through `optimise`, the long book still rewards its highest score while the
    short book rewards its lowest (`test_optimise_tilts_strongest_short`)
  - A single short takes the whole gross target, so the multiplier cannot inflate gross
    (`test_single_short`)
  - An empty short book is returned as is (`test_empty_book_unchanged`)

## Notes
- Against `main`, `test_optimise_tilts_strongest_short` fails on the bug itself: S19 (score
  40) weighted 0.0434 against S00 (score 2) 0.0289. The direct tilt tests fail there because
  `ascending` does not exist.
- `tests/test_mvo_optimizer.py` passes unchanged (MVO uses the conviction optimiser for its cost
  proxy and fallback).

## Commands
```bash
make lint
make test
make security
```
