## Summary

The 30% per-run turnover budget counted building the book from cash as turnover. On
2026-11-04 the book was 27% gross against a 150% target; the budget kept 16 of 40 trades
(28.6% of NAV) and dropped 24, 22 of them new names, with no record of what was dropped.

- `portfolio/rebalance.py`: per book, trades that move the book toward its target gross
  (opens and adds below target; reductions and closures above it) are exempt from the budget
  up to `|target gross - current gross|`. The 30% now limits rotation only. The allowance is
  per book and tied to the gap's direction, so it cannot pay for rotation or for the other
  book.
- Trades the budget does trim are written to `position_approvals` as `TRIMMED` with their
  original action and size; nothing downstream reads them.
- `run_portfolio.py` prints a turnover summary (needed, build-up allowance, rotation budget,
  kept and trimmed, in $ and % of NAV).
- Spec 04 AC7 restated to the shipped behaviour (conviction ranking, skip-if-not-fit,
  allowance, `TRIMMED`).

## Spec

- Spec: `docs/specs/57-turnover-build-up.md`
- Test plan: `docs/test-plans/57-turnover-build-up.md`
- PR draft path: `.ai/pr-description/57-turnover-build-up.md`

## Acceptance Criteria

- [x] AC1: Empty book, 40-name 150% target, 30% budget -> 40 trades, none trimmed.
- [x] AC2: Full book at target gross: rotation still capped at the budget.
- [x] AC3: Partly built book: rotation budget plus each book's own gap; no cross-book spending.
- [x] AC4: De-risking reductions toward a lower target gross are not trimmed.
- [x] AC5: Closures never trimmed; they spend the rotation budget first unless de-risking.
- [x] AC6: Trimmed trades written as `TRIMMED`; pre-trade and the executor ignore them.
- [x] AC7: Turnover summary printed by Layer 4.
- [x] AC8: What-if writes nothing, including `TRIMMED` rows.
- [x] AC9: Spec 04 AC7 and its turnover section describe the shipped behaviour.

## Security Review

- [x] Security considerations reviewed in `docs/specs/57-turnover-build-up.md`
- [x] No meaningful security impact beyond larger order lists on build-up nights; every order
  still passes the Layer 5 pre-trade veto and circuit breakers.
- Reviewer focus: `_budget_values` can never exempt more than the gap per book (tests
  `test_small_gap_cannot_unlock_rotation`, `test_long_gap_not_spent_by_short_book`).

## Validation

- Tests: 9 new/updated tests failed before the change and pass after (`TestBuildUpAllowance`,
  `TestTrimmedRows`); the two empty-book budget tests were reworked into full-book rotations,
  since a build from cash is no longer trimmed.
- Read-only replay of the 2026-11-04 Layer 4 run (optimiser plus `generate_trades`
  `commit=False` in a READ ONLY transaction; corrected credit z 2.57, so CAUTION, 120% target):
  - before this packet (with 58): 17 of 40 trades, 28.6% of NAV, 51.2% gross if all filled;
  - with this packet: 38 of 40 trades (2 already at target), 96.0% of NAV, nothing trimmed,
    118.6% gross if all filled (target 120%).
- `make lint`: pass. `make test`: pass (counts in the phase-2 summary).

## GitHub Checks

- Required checks for `main`: `CI / test`, `CodeQL / analyze`

## Changelog

- [x] `CHANGELOG.md` under `## Unreleased`.

## Open Risks

- The first night after deploy sends close to the whole book (~96% of NAV, ~38 orders) on one
  night's scores. Run `python run_portfolio.py --whatif` by hand first.
- Found while testing, not fixed here: `map_action` never covers a short whose name leaves the
  target (cur < 0, target 0 -> HOLD). No current short is affected; pinned by a strict xfail
  test and added to `docs/backlog.md` as high priority.
- A `TRIMMED` row can still be approved by hand from the dashboard research page.
