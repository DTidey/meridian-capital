## Summary

A held short whose target dropped to 0 mapped to HOLD in `map_action` (the COVER branch
required `tgt < 0`), so a short leaving the target book was never covered by Layer 4. The long
mirror already mapped to SELL.

- `portfolio/rebalance.py` `map_action`: COVER when `cur < 0`, `tgt <= 0`, `delta > 0`.
- `portfolio/rebalance.py` `generate_trades`: ValueError on duplicate tickers in the current
  book (last-wins would size a cover from one lot).
- Strict-xfail removed from `test_short_dropped_from_target_is_covered`; backlog entry closed.
- No executor, pre-trade or turnover change: tests show a target-0 cover is a closure in each.

## Spec

- Spec: `docs/specs/61-short-cover-target-zero.md`
- Test plan: `docs/test-plans/61-short-cover-target-zero.md`
- PR draft path: `.ai/pr-description/61-short-cover-target-zero.md`

## Acceptance Criteria

- [x] AC1: A short with target 0 maps to COVER for the full current quantity.
- [x] AC2: A long with target 0 still maps to SELL.
- [x] AC3: All other `map_action` cases (partial reductions, increases, flips, HOLD) unchanged.
- [x] AC4: Executor submits the cover as BUY / BUY_TO_CLOSE, full quantity, close x 1.015 limit.
- [x] AC5: Pre-trade approves it as closing (also under halt); the turnover budget never trims it.
- [x] AC6: Duplicate tickers in the current book raise ValueError.

## Security Review

- [x] Security considerations reviewed in `docs/specs/61-short-cover-target-zero.md`
- No auth, secrets or input-surface change. Layer 6 can now send BUY_TO_CLOSE orders for
  shorts leaving the target; size is capped at the held quantity.

## Validation

- 12 tests failed before the fix (11 new plus the former xfail) and pass after.
- `make lint`, `make test`, `make security`: pass.
- Read-only book check on 2026-11-05 (Alpaca GET positions, DB SELECTs in a READ ONLY
  transaction): all 10 current shorts are SHORT candidates on the 2026-11-04 scores (ranks 1-15
  of 97) and had non-zero targets that night, so none was being left uncovered.

## GitHub Checks

- Required checks for `main`: `CI / test`, `CodeQL / analyze`

## Changelog

- [x] `CHANGELOG.md` under `## Unreleased`.

## Open Risks

- Short to long in one night is one BUY (`BUY_TO_OPEN`) for cover plus open; whether Alpaca
  accepts an opening intent larger than the open short is not verified.
- A held name with no stored close for the score date is still skipped by the executor
  (`no_close`), so a short that has left the data universe entirely cannot be covered by the
  pipeline.
