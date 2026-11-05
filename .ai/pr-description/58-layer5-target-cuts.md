## Summary

On 2026-11-04 Layer 5 cut the submitted trades from 28.6% to 16.1% of NAV and reversed 4 of
16 (NKE, CLX, AKAM, CF): the earnings blackout halved each traded name's *target*, then
tail-risk REDUCE_GROSS_20 scaled it by 0.8, but only for names trading that night, and Layer 4
planned the full target again the next night.

- Tail risk is now a book-level gross scale in Layer 4: `run_portfolio.py` evaluates the
  regime read-only (`risk.tail_risk.evaluate_tail_risk`) and scales both book targets
  (`portfolio.book_targets.scale_book_targets`): 150% in NORMAL, 120% in CAUTION, 75% in
  STRESS. Every position honours the cut and the next night plans the same book.
- Layer 5 tail risk records the regime and no longer edits `position_approvals`
  (`_apply_reduce_gross` removed).
- The earnings haircut has one owner, the Layer 4 optimiser (forward 5-day window, unchanged).
  Pre-trade check 2 no longer resizes; earnings-window names go through checks 3-8 (they used
  to skip them) and approvals are tagged `EARNINGS_WINDOW`.
- Packet 56's "a resize that turns an opening trade into a reduction is wanted" is superseded
  for pre-trade and tail risk (owner sign-off); `map_action`, breaker SIZE_DOWN and the
  executor guards are unchanged.

## Spec

- Spec: `docs/specs/58-layer5-target-cuts.md`
- Test plan: `docs/test-plans/58-layer5-target-cuts.md`
- PR draft path: `.ai/pr-description/58-layer5-target-cuts.md`

## Acceptance Criteria

- [x] AC1: `evaluate_tail_risk` matches `run_tail_risk`'s regime and writes nothing.
- [x] AC2: Layer 4 gross targets scaled: 150% NORMAL, 120% CAUTION (64/56), 75% STRESS (40/35).
- [x] AC3: Layer 5 tail risk edits no approval row; the regime is still recorded.
- [x] AC4: Under CAUTION, a filled night-1 book produces no trades on night 2 with the same inputs.
- [x] AC5: 11-04 regression: every opening row keeps Layer 4's action and size through Layer 5.
- [x] AC6: Pre-trade check 2 no longer resizes; window names fully checked and tagged `EARNINGS_WINDOW`.
- [x] AC7: Optimiser earnings haircut unchanged.
- [x] AC8: What-if: Layer 4 evaluates and prints the regime, writes nothing.
- [x] AC9: Specs 04, 05, 56 and the backlog updated (SIZE_DOWN and POSITION_SIZE items added).

## Security Review

- [x] Security considerations reviewed in `docs/specs/58-layer5-target-cuts.md`
- [x] Input validation, data exposure, file access, network access, or dependencies changed:
  Layer 4 now makes one read-only FRED request per run (same code path and key as Layer 5)
- Reviewer focus: `FRED_API_KEY` is never logged (test); pre-trade is stricter, not looser
  (earnings names now run every check); `scale_book_targets` refuses a scale outside (0, 1]
  and a scaled book outside the configured net/gross bounds.

## Validation

- New tests failed before the change and pass after: `TestEvaluateTailRisk`,
  `TestNoApprovalEdits`, `TestEarningsWindow`, `tests/test_run_portfolio.py`,
  `tests/test_layer5_regression_2026_11_04.py`. The regression test on the old code
  reproduces the 11-04 rows (NKE COVER to -40, AKAM COVER to -7, AVY 19, MU 4).
- `make lint`: pass. `make test`: pass (counts in the phase-2 summary).

## GitHub Checks

- Required checks for `main`: `CI / test`, `CodeQL / analyze`

## Changelog

- [x] `CHANGELOG.md` under `## Unreleased`.

## Open Risks

- Entering CAUTION with a full book trims every position about 20% in one night (intended).
  Until packet 57 lands those reductions spend the 30% turnover budget.
- Layer 4 and Layer 5 each evaluate the regime; if FRED fails in one and not the other they
  can log different states. Only Layer 4's affects the book.
- Circuit-breaker SIZE_DOWN_30 still scales tonight's targets (backlog).
