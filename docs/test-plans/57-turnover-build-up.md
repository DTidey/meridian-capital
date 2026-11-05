# Test Plan: turnover-build-up

Path: `docs/test-plans/57-turnover-build-up.md`
Spec: `docs/specs/57-turnover-build-up.md`
Status: implemented with the approved options (D1-A allowance, D2-A TRIMMED rows, D3 spec 04
AC7 restated).

## What changed
- `portfolio/rebalance.py`: per-book build-up allowance in `_apply_turnover_budget`; trimmed
  rows carried to `_write_approvals` and written as `TRIMMED`.
- `run_portfolio.py` `_print_turnover_summary`: turnover summary from
  `trades.attrs["turnover"]`.
- Spec 04 AC7 text.

## Acceptance criteria coverage
- AC1: `tests/test_rebalance.py::TestBuildUpAllowance::test_build_from_cash_not_trimmed`
  (40 names, 150% gross, empty book, 30% budget -> 40 non-HOLD trades; the investigation's
  `test_build_from_cash_not_capped_by_turnover_budget` repro moves here without xfail)
- AC2: `TestBuildUpAllowance::test_full_book_rotation_still_capped` (full long book rotating
  50% of NAV in place plus a closure matched by a new name of equal size -> kept value <=
  20% NAV including the closure).
  `TestTurnoverBudget::test_turnover_budget_trims_smallest_score_changes` and
  `test_budget_split_preserves_both_books` reworked from empty-book builds (no longer
  trimmed) into full-book rotations
- AC3: `TestBuildUpAllowance::test_partial_book_gap_per_side` (long book 4% -> 50%, short
  book 2% -> 40%, 20% budget -> all 20 trades kept);
  `test_long_gap_not_spent_by_short_book` (long book full and rotating 50%, short book empty
  with a 20% build-up; the long rotation is capped at the 20% budget and trimmed while all
  short opens pass)
- AC4: `TestBuildUpAllowance::test_de_risking_reductions_not_trimmed` (book 150%, target 120%
  -> all reductions kept although they exceed 30%)
- AC5: existing `TestTurnoverBudget::test_closures_are_never_trimmed` (unchanged, must pass)
- AC6: `TestTrimmedRows::test_trimmed_rows_written` (status, original action, target and
  delta); `tests/test_risk_pre_trade.py::TestTrimmedIgnored::test_trimmed_not_evaluated`;
  `tests/test_execution_executor.py::TestTrimmedIgnored::test_no_order_for_trimmed`
- AC7: `tests/test_run_portfolio.py::TestTurnoverSummary::test_turnover_summary_printed`
  (capsys; figures as % of NAV and $), `test_summary_missing_is_silent`;
  `tests/test_rebalance.py::TestTrimmedRows::test_summary_attached`
- AC8: `TestTrimmedRows::test_whatif_writes_nothing`
- AC9: documentation review of the spec 04 diff

## Edge cases
- From spec:
  - Empty target, all closures (`TestBuildUpAllowance::test_all_closures_pass`, long book;
    the short-book case is the strict xfail
    `TestBuildUpAllowance::test_short_dropped_from_target_is_covered`, a pre-existing
    `map_action` bug found here and backlogged)
  - One book at target, the other empty (covered by `test_long_gap_not_spent_by_short_book`)
  - Missing price (0.0) neither spends nor creates allowance
    (`TestBuildUpAllowance::test_missing_price_no_allowance`)
  - Boundary: remaining trade value equal to the allowance is kept
    (`TestBuildUpAllowance::test_exact_fit_kept`)
- Additional adversarial cases:
  - Full book that rotates *and* grows by 1%: the allowance is 1% of NAV, not the whole
    rotation; a 50% rotation is still capped at 21% (20% budget + 1%)
    (`TestBuildUpAllowance::test_small_gap_cannot_unlock_rotation`)
  - Gap created by a target book that is *net* larger on one side only: allowance follows each
    side's own gap, not total gross (long +20%, short -20%, total unchanged -> both sides get
    20%, nothing is "free" beyond those gaps)
    (`TestBuildUpAllowance::test_offsetting_side_gaps_counted_per_side`)
  - Short positions stored with negative shares and SHORT targets with negative shares: the
    gap uses absolute values (a short book of -$30k vs target -$70k is a $40k build-up, not a
    $40k de-risking)
    (`TestBuildUpAllowance::test_short_gap_uses_absolute_value`)
  - Re-running Layer 4 for the same date with `commit=True` writes TRIMMED rows again: test
    pins current append behaviour so a later dedupe fix is deliberate
    (`TestTrimmedRows::test_rerun_appends`)
  - NAV <= 0 in config: no trimming and no division error (`test_zero_nav_no_trim`)

## Notes
- Flaky risks: none; in-memory SQLite and synthetic prices.
- Determinism considerations: `generate_trades` iterates a `set` of tickers; tests assert on
  sets of kept/trimmed tickers, not row order.

## Commands
```bash
make lint
make test
make security
```
