# Test Plan: churn-hysteresis

Path: `docs/test-plans/64-churn-hysteresis.md`
Spec: `docs/specs/64-churn-hysteresis.md`
Status: Planned (spec-only packet; the Tester role fills in results with the implementation)

## What will change
- New `portfolio/book_selection.py` (`select_book`), used by `portfolio/optimizer.py` and
  `portfolio/mvo_optimizer.py`; `run_portfolio.py` loads positions before optimising.
- `factors/regime_weights.py`: smoothed VIX, regime shares, weight blend, label.
- `factors/loader.py` `_load_vix`: 14-calendar-day window.
- `run_scoring.py` and `data/db.py`: three new nullable `regime_state` columns.
- `portfolio/rebalance.py` `_budget_values`: rotation credit.
- New `portfolio/churn_replay.py` (read-only replay).

## Acceptance criteria coverage
- AC1: `tests/test_book_selection.py::TestSelectBook::test_incumbent_within_exit_rank_kept`,
  `test_fill_with_best_non_held`, `test_book_size_fixed`
- AC2: `TestSelectBook::test_beyond_exit_rank_closed`, `test_neutral_incumbent_closed`,
  `test_flipped_incumbent_closed_on_old_side`,
  `tests/test_rebalance.py::test_buffer_exit_generates_full_closure`
- AC3: `TestSelectBook::test_exit_rank_equal_n_matches_top_n`,
  `test_missing_key_matches_top_n`, `test_empty_book_top_n`
- AC4: `TestSelectBook::test_tie_break_by_ticker`, `test_row_order_independent`
- AC5: `tests/test_optimizer.py::test_select_book_used_and_gross_preserved`,
  `tests/test_mvo_optimizer.py::test_select_book_used`
- AC6: `tests/test_regime_weights.py::TestBlend::test_neutral_zone_base_weights`,
  `test_full_edge_equals_regime_weights`, `test_midpoint_blend`, `test_weights_sum_to_one`,
  `test_smoothed_vix_mean_of_last_n`, `test_window_gap_uses_available_closes`
- AC7: `tests/test_regime_weights.py::TestLabel::test_label_thresholds`,
  `test_no_vix_normal_with_warning`, `tests/test_regime_state.py::test_regime_state_columns`,
  `test_conditional_weights_off`
- AC8: `tests/test_rebalance.py::TestRotationCredit::test_replacement_exempt_up_to_closure`,
  `test_closure_never_trimmed`, `test_no_closures_unchanged`, `test_credit_per_book_not_shared`
- AC9: `tests/test_churn_replay.py::test_read_only_transaction`,
  `test_metrics_on_fixture_history`, `test_baseline_matches_top_n`
- AC10: `tests/test_book_selection.py::test_exit_rank_below_n_raises`,
  `tests/test_regime_weights.py::test_band_order_validated`, `test_smoothing_days_validated`

## Edge cases
- From spec:
  - Tiny incumbent (partial fill): kept, target is full weight
    (`TestSelectBook::test_tiny_incumbent_kept`)
  - Held count above `num_*`: capped by rank, rest closed
    (`TestSelectBook::test_oversized_side_capped`)
  - Fewer candidates than `num_*`: all selected, incumbents first
    (`TestSelectBook::test_short_candidate_list`)
  - Name dropped from the universe: closed, budget value 0 without a price
    (`TestSelectBook::test_missing_score_row_closed`)
  - VIX gap and single close in window (`TestBlend::test_single_close_with_warning`)
  - Backfill gives the same weights as the first run (`TestBlend::test_stateless_rerun`)
- Additional adversarial cases (planned):
  - Replay of the 2026-11-06 -> 2026-11-09 fixture: APA (rank 5 -> 21) kept, ATO (18 -> 85)
    and DE (12 -> 74) closed, VICI (LONG -> NEUTRAL) closed
    (`TestSelectBook::test_2026_11_09_fixture`)
  - Same ticker held long and listed as a SHORT candidate at rank 1: closed on the long side,
    enters the short book only through the top-N rule, one trade row
    (`TestSelectBook::test_flip_to_top_short`)
  - Incumbent blocked from tilt: a buffered name at rank 30 never receives the top-5% or
    top-10% multiplier (`tests/test_optimizer.py::test_buffered_name_gets_no_tilt`)
  - Rotation credit cannot exceed the closure value or cross books: a long closure does not
    exempt a short open (`TestRotationCredit::test_credit_per_book_not_shared`)
  - Band edges on exact boundaries (VIX mean exactly 20 and 30; 12 and 18) give shares 0 and 1
    (`TestBlend::test_exact_edges`)
  - VIX closes dated after the score date are ignored by the smoothing window (no lookahead)
    (`TestBlend::test_future_vix_ignored`)

## Notes
- Determinism: assert by ticker, not by row position; shuffle inputs in AC4 tests.
- Replay tests use a SQLite fixture DB; the read-only check asserts the transaction mode and
  that no table row counts change.

## Commands
```bash
make lint
make test
make security
```
