# Test Plan: turnover-build-up

Path: `docs/test-plans/57-turnover-build-up.md`
Spec: `docs/specs/57-turnover-build-up.md`
Status: planned (phase 1). Test names below are written in phase 2 against the owner's D1-D3
choices; they assume the recommended options.

## What changes (planned)
- `portfolio/rebalance.py`: per-book build-up allowance in `_apply_turnover_budget`; trimmed
  rows carried to `_write_approvals` and written as `TRIMMED`.
- `run_portfolio.py`: turnover summary.
- Spec 04 AC7 text.

## Acceptance criteria coverage
- AC1: `tests/test_rebalance.py::TestBuildUpAllowance::test_build_from_cash_not_trimmed`
  (40 names, 150% gross, empty book, 30% budget -> 40 non-HOLD trades; the investigation's
  `test_build_from_cash_not_capped_by_turnover_budget` repro moves here without xfail)
- AC2: `TestBuildUpAllowance::test_full_book_rotation_still_capped` (book at 150%, target
  rotates 10 names = 50% of NAV, gross unchanged -> kept value <= 30% NAV + closures)
- AC3: `TestBuildUpAllowance::test_partial_book_gap_per_side` (book 27%, target 150% ->
  kept value <= 30% + gap, all opening trades kept);
  `test_long_gap_not_spent_by_short_book` (long book full, short book empty; a long rotation
  above 30% is still trimmed while all short opens pass)
- AC4: `TestBuildUpAllowance::test_de_risking_reductions_not_trimmed` (book 150%, target 120%
  -> all reductions kept although they exceed 30%)
- AC5: existing `TestTurnoverBudget::test_closures_are_never_trimmed` (unchanged, must pass)
- AC6: `TestTrimmedRows::test_trimmed_rows_written` (status, original action, target and
  delta); `tests/test_risk_pre_trade.py::TestTrimmedIgnored::test_trimmed_not_evaluated`;
  `tests/test_execution_executor.py::TestTrimmedIgnored::test_no_order_for_trimmed`
- AC7: `tests/test_run_portfolio.py::test_turnover_summary_printed` (capsys; figures as % of
  NAV and $)
- AC8: `TestTrimmedRows::test_whatif_writes_nothing`
- AC9: documentation review of the spec 04 diff

## Edge cases
- From spec:
  - Empty target, all closures (`TestBuildUpAllowance::test_all_closures_pass`)
  - One book at target, the other empty (covered by `test_long_gap_not_spent_by_short_book`)
  - Missing price (0.0) neither spends nor creates allowance
    (`TestBuildUpAllowance::test_missing_price_no_allowance`)
  - Boundary: remaining trade value equal to the allowance is kept
    (`TestBuildUpAllowance::test_exact_fit_kept`)
- Additional adversarial cases:
  - Full book that rotates *and* grows by 1%: the allowance is 1% of NAV, not the whole
    rotation; a 60% rotation is still capped near 31%
    (`TestBuildUpAllowance::test_small_gap_cannot_unlock_rotation`)
  - Gap created by a target book that is *net* larger on one side only: allowance follows each
    side's own gap, not total gross (long +20%, short -20%, total unchanged -> both sides get
    20%, nothing is "free" beyond those gaps)
    (`TestBuildUpAllowance::test_offsetting_side_gaps_counted_per_side`)
  - Short positions stored with negative shares and SHORT targets with negative shares: the
    gap uses absolute values (a short book of -$30k vs target -$70k is a $40k gap, not -$40k)
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
