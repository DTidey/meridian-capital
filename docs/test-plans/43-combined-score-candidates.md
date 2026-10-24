# Test Plan: combined-score-candidates

Path: `docs/test-plans/43-combined-score-candidates.md`

## What changed
- `analysis/combined_score.py` `compute_combined_scores`: direction from Layer 2; within each
  side, quant and AI mid-rank percentiles blended 60/40 and mapped onto the side's band; NEUTRAL
  names keep their quant composite; sector re-rank removed. New helper `_strength_rank`;
  `_load_quant_scores` also loads `direction`.
- `tests/test_combined_score.py`: the `compute_combined_scores` tests are replaced (they
  encoded the packet 03 re-rank); `compute_ai_composite` tests unchanged.

## Acceptance criteria coverage
- AC1: `TestDirection::test_direction_follows_quant`,
  `test_neutral_name_never_promoted_by_bad_ai_on_candidates`,
  `test_candidate_never_demoted_by_bad_ai`
- AC2: `TestBands::test_scores_within_side_bands`, `test_neutral_keeps_quant_score`,
  `test_unnormalised_weights_stay_in_band`
- AC3: `TestOrdering::test_better_ai_ranks_long_higher`, `test_better_ai_ranks_short_lower`,
  `test_zero_ai_weight_is_quant_order`, `test_zero_ai_weight_is_quant_order_for_shorts`,
  `test_zero_quant_weight_is_ai_order`, `test_blend_matches_formula`,
  `test_blend_matches_formula_for_shorts`
- AC4: `TestMissingAi::test_candidate_without_ai_row_gets_neutral_rank`,
  `test_zero_analyzers_treated_as_missing`, `test_single_candidate_side`,
  `test_no_ai_results_at_all`
- AC5: `TestPersistence::test_rows_written`, `test_empty_quant_returns_empty`

## Edge cases
- From spec: ties (equal quant scores in `test_better_ai_ranks_long_higher` and
  `test_candidate_without_ai_row_gets_neutral_rank`); one candidate per side
  (`test_single_candidate_side`); no AI anywhere (`test_no_ai_results_at_all`); unnormalised weights
  (`test_unnormalised_weights_stay_in_band`); `analyzers_used == 0` with a stale composite
  (`test_zero_analyzers_treated_as_missing`).
- Additional adversarial cases:
  - Unreviewed NEUTRAL names at 79.9 and 20.1 stay NEUTRAL when the only candidates have the
    worst possible AI (`TestDirection::test_neutral_name_never_promoted_by_bad_ai_on_candidates`)
  - A sector of five NEUTRAL names is not re-ranked into a LONG
    (`TestDirection::test_sector_does_not_affect_direction`)
  - Bunched AI (5.9 vs 6.1) moves the order as much as a wide gap, since only ranks count
    (`TestOrdering::test_raw_ai_scale_does_not_matter`)
  - A stale `ai_composite` of 10 on a row with `analyzers_used == 0` is ignored
    (`TestMissingAi::test_zero_analyzers_treated_as_missing`)

## Notes
- Dropping the SHORT sign on the quant rank fails `test_zero_ai_weight_is_quant_order_for_shorts`
  and `test_blend_matches_formula_for_shorts` (checked by mutation).
- Against `main`'s `compute_combined_scores`, 11 of the new tests fail (direction, bands,
  formula, missing AI, persistence of the quant score for NEUTRAL names).
- Production dry run on 2026-10-23 data (writes stubbed out): direction equals Layer 2
  direction for all 503; LONG/SHORT names without AI review 68 -> 0; LONG 81.6-99.1, SHORT
  1.3-18.6; PLD NEUTRAL 26.7 (was SHORT 13.3); NEE SHORT 14.3 (was NEUTRAL 29.0).

## Commands
```bash
make lint
make test
make security
```
