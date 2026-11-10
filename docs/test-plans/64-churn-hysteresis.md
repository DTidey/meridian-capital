# Test Plan: churn-hysteresis

Path: `docs/test-plans/64-churn-hysteresis.md`
Spec: `docs/specs/64-churn-hysteresis.md`
Status: Implemented (2026-11-10). AC1-AC9 after Decision 2 removed the rotation credit and
renumbered the ACs (old AC9 replay -> AC8, old AC10 config -> AC9).

## What changed
- New `portfolio/book_selection.py` (`select_book`, `classify_exits`, `exit_rank`), used by
  `portfolio/optimizer.py` and `portfolio/mvo_optimizer.py`; `run_portfolio.py` passes the
  broker positions it already loads (packet 63) to the optimiser and validates the exit rank.
- `factors/regime_weights.py`: smoothed VIX, regime shares, weight blend, label, `resolve`,
  config validation; the hard-coded `resolve_regime` is removed.
- `factors/loader.py` `_load_vix`: window max(14, 2 x smoothing days) calendar days.
- `run_scoring.py`, `factors/db.py`, `data/db.py`: three new nullable `regime_state` columns.
- New `portfolio/churn_sim.py` and `portfolio/churn_replay.py` (read-only replay).
- `config.yaml`: `incumbent_exit_rank: 35`, `vix_smoothing_days: 5`, band edges; `vix_below`
  and `vix_above` removed.

## Acceptance criteria coverage
- AC1: `tests/test_book_selection.py::TestSelectBook::test_incumbent_within_exit_rank_kept`,
  `test_fill_with_best_non_held`, `test_book_size_fixed`
- AC2: `TestSelectBook::test_beyond_exit_rank_closed`, `test_neutral_incumbent_closed`,
  `test_flipped_incumbent_closed_on_old_side`,
  `tests/test_rebalance.py::test_buffer_exit_generates_full_closure`
- AC3: `TestSelectBook::test_exit_rank_equal_n_matches_top_n`,
  `test_missing_key_matches_top_n`, `test_empty_book_top_n`,
  `tests/test_optimizer.py::test_no_current_matches_top_n`
- AC4: `TestSelectBook::test_tie_break_by_ticker`, `test_row_order_independent`
- AC5: `tests/test_optimizer.py::test_select_book_used_and_gross_preserved`,
  `tests/test_mvo_optimizer.py::test_select_book_used`
- AC6: `tests/test_regime_weights.py::TestBlend::test_neutral_zone_base_weights`,
  `test_full_edge_equals_regime_weights`, `test_midpoint_blend`, `test_weights_sum_to_one`,
  `test_smoothed_vix_mean_of_last_n`, `test_window_gap_uses_available_closes`
- AC7: `tests/test_regime_weights.py::TestLabel::test_label_thresholds`,
  `test_no_vix_normal_with_warning`, `test_conditional_weights_off`,
  `test_full_high_vol_label_and_close`, `tests/test_regime_state.py::test_regime_state_columns`
- AC8: `tests/test_churn_replay.py::test_read_only_transaction`,
  `test_metrics_on_fixture_history`, `test_baseline_matches_top_n`,
  `test_flip_counted_separately`, `test_spearman_pairs`,
  `test_postgres_read_only_statement_first`, `test_unknown_dialect_refused`,
  `test_switch_rescore_matches_stored_regime_weights`
- AC9: `tests/test_book_selection.py::TestExitRankConfig::test_exit_rank_below_n_raises`,
  `test_exit_rank_not_integer_raises`, `test_exit_rank_uses_larger_side`,
  `test_default_is_book_size`, `test_select_book_validates`,
  `tests/test_regime_weights.py::TestRegimeConfig::test_band_order_validated`,
  `test_smoothing_days_validated`, `test_touching_bands_allowed`, `test_defaults_when_missing`,
  `test_shipped_config_valid`, `tests/test_regime_state.py::test_bad_band_stops_scoring_before_db`,
  `tests/test_mvo_optimizer.py::test_bad_exit_rank_raises_before_fallback`

## Edge cases
- From spec:
  - Tiny incumbent (partial fill): kept, target is full weight
    (`TestSelectBook::test_tiny_incumbent_kept`)
  - Held count above `num_*`: capped by rank, rest closed with reason `capped`
    (`TestSelectBook::test_oversized_side_capped`)
  - Fewer candidates than `num_*`: all selected, incumbents first
    (`TestSelectBook::test_short_candidate_list`)
  - Name dropped from the universe: closed, reason `out_of_band`
    (`TestSelectBook::test_missing_score_row_closed`)
  - VIX gap and single close in window (`TestBlend::test_single_close_with_warning`,
    `test_window_gap_uses_available_closes`)
  - Rerun gives the same weights, whatever the row order of the VIX frame
    (`TestBlend::test_stateless_rerun`)
- Adversarial cases:
  - 2026-11-06 -> 2026-11-09 fixture (spec table ranks, 108 LONG / 96 SHORT candidates, the
    shipped `config.yaml` exit rank): APA (rank 5 -> 21) kept, ATO (18 -> 85) and DE
    (12 -> 74) closed beyond the exit rank, VICI, YUM and CRL (now NEUTRAL) closed out of
    band, every other name kept exactly when its rank <= the exit rank, 20 names per side
    (`TestSelectBook::test_2026_11_09_fixture`)
  - Same ticker held long and listed as the top SHORT candidate: closed on the long side,
    enters the short book only by the top-N rule (not an incumbent), one row
    (`TestSelectBook::test_flip_to_top_short`)
  - Zero or missing (NaN) share counts are not held on either side
    (`TestSelectBook::test_zero_or_missing_shares_not_held`)
  - A held short at a buffer rank on the LONG list is not a long incumbent
    (`TestSelectBook::test_held_short_not_incumbent_on_long_side`)
  - A buffered name at the bottom of the book never receives the top-5%/10% multiplier
    (`tests/test_optimizer.py::test_buffered_name_gets_no_tilt`)
  - Band edges on exact boundaries (20 and 30; 12 and 18) give shares 0 and 1
    (`TestBlend::test_exact_edges`)
  - VIX closes dated after the score date are ignored (no lookahead)
    (`TestBlend::test_future_vix_ignored`)
  - The 2026-11-04 .. 11-09 VIX episode moves the high_vol share by less than 0.2 a night and
    keeps the NORMAL label (`TestBlend::test_episode_moves_gradually`)
  - Bad exit rank under MVO raises instead of falling back silently to conviction tilt
    (`tests/test_mvo_optimizer.py::test_bad_exit_rank_raises_before_fallback`)
  - Selection log names buffered tickers and closure reasons
    (`TestSelectBook::test_selection_logged`)
- Removed with Decision 2: the rotation credit tests (`TestRotationCredit::*`) move to the
  follow-up packet.

## Notes
- Determinism: assertions by ticker, not row position; inputs shuffled in AC4 tests.
- Replay DB tests use a SQLite fixture; the read-only test runs a full replay and checks that
  no row count changes, then checks that a DELETE on the read-only connection raises. The
  PostgreSQL statement is checked on a mock connection (first statement is
  `SET TRANSACTION READ ONLY`).
- The replay's rescoring was also checked against production read-only (2026-11-10): the
  recomputed hard switch equals the stored results on every metric for 2026-10-27 to 11-09.
- `tests/test_held_prices_layer4.py`: the optimiser spy accepts the new `current` argument.

## Commands
```bash
make lint
make test
make security
```

## Results (2026-11-10)
- `make lint`: pass.
- `make test`: 1770 passed.
- `make security`: pass (bandit no issues, pip-audit no known vulnerabilities).
