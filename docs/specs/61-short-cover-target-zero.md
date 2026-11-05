# Short Cover When the Target Drops to Zero

**Spec file:** `docs/specs/61-short-cover-target-zero.md`
**Spec slug:** short-cover-target-zero
**Status:** Done
**Owner:** DTidey
**Date:** 2026-11-05

## Problem statement
`portfolio/rebalance.py` `map_action` has no branch for a held short whose target is 0
(`cur < 0`, `tgt == 0`): the COVER branch requires `tgt < 0`, so the row falls through to
HOLD. A short whose name leaves the target book (no longer a SHORT candidate, or not picked by
the optimiser) is therefore never covered by Layer 4; it stays open until something else (the
circuit breaker) closes it. The long-side mirror (`cur > 0`, `tgt == 0`) already maps to SELL.

Found by packet 57's closure test and pinned as a strict xfail
(`tests/test_rebalance.py::TestBuildUpAllowance::test_short_dropped_from_target_is_covered`);
backlog entry "A short that leaves the target is never covered", priority high. No harm so far:
every short held on 2026-11-04 was still in that night's target, and the only cover-to-zero
approvals ever written (AKAM, CLX on 2026-10-28) came from the circuit breaker.

## Scope
In scope:
- `map_action`: COVER when `cur < 0`, `tgt <= 0` and `delta > 0` (the mirror of the SELL
  branch `tgt >= 0 and cur > 0 and delta < 0`).
- `generate_trades`: refuse duplicate tickers in `current` with `ValueError` (last-wins would
  size a cover from one lot only). Duplicate tickers in `target` already raise in pandas
  (`to_dict(orient="index")` on a non-unique index); pinned by a test, no code change.
- Remove the strict-xfail marker; update the backlog entry.

Out of scope / non-goals:
- Executor, pre-trade and turnover code: they already treat SELL/COVER and `target_shares == 0`
  as closures (packets 57, 60); this packet only adds tests that a target-0 cover reaches them
  correctly.
- A single order that crosses zero (short to long or long to short in one night): Layer 4 emits
  one BUY or SHORT row for the whole delta, as today. Whether Alpaca accepts a
  `BUY_TO_OPEN` larger than an open short is not verified here (noted under open risks).
- A held name with no stored close for the score date: the executor still skips it
  (`no_close`, packet 60 AC3), rather than guessing a limit.

## Assumptions
- Targets are whole shares (`round()` in `generate_trades`, packet 56), so `tgt == 0` is exact
  and `tgt <= 0` with `cur < 0` and `delta > 0` covers both "reduce the short" and "close it".
- `portfolio_positions.ticker` is the primary key, so duplicate tickers in `current` cannot come
  from the database today; the guard protects other callers and future changes.

## Proposed behavior / API
```python
map_action(cur=-10, tgt=0, delta=10)   # "COVER" (was "HOLD")
map_action(cur=10, tgt=0, delta=-10)   # "SELL"  (unchanged)
map_action(cur=-10, tgt=-5, delta=5)   # "COVER" (unchanged)
map_action(cur=-10, tgt=5, delta=15)   # "BUY"   (unchanged: one row, whole delta)
```
`generate_trades` signature and return columns unchanged.

## Acceptance criteria
- AC1: A held short whose target is 0 (name absent from the target, or present with 0 shares,
  or a sub-share target that rounds to 0) maps to COVER with `delta_shares` equal to the full
  current quantity (`-current_shares`) and `target_shares` 0.
- AC2: A held long whose target is 0 still maps to SELL for the full quantity.
- AC3: Every other `map_action` case is unchanged: open/add/reduce long, open/add/reduce short,
  short to long (BUY), long to short (SHORT), and HOLD when the delta is under one share.
- AC4: The executor submits a target-0 cover from a committed approval as a BUY with position
  intent `BUY_TO_CLOSE`, quantity equal to the full short, and a limit of the score date's
  close x (1 + `execution.limit_slippage_pct`) (packet 60).
- AC5: Pre-trade approves a target-0 cover as a closing trade (also under halt lock), and the
  turnover budget never trims it (packet 57: closures are never trimmed); it is ordered first.
- AC6: `generate_trades` raises `ValueError` naming the ticker when `current` holds the same
  ticker twice.

## Security considerations
- Auth/authz impact: none.
- Input handling or injection risk: none; AC6 adds a fail-loud check on duplicate input rows.
- Secrets or credential handling: none.
- Data exposure or privacy impact: none.
- File system access impact: none.
- Network or external service impact: Layer 6 may now send BUY_TO_CLOSE orders for shorts that
  leave the target. Each one reduces exposure; sizes are capped at the held quantity.
- Dependency or supply-chain impact: none.
- Security notes for reviewers/testers: confirm a cover can never exceed the held short
  (delta = 0 - current) and cannot flip the position.

## Edge cases
- Name left the scored universe: Layer 4 loads prices for candidates only, so the row has
  price 0 and cost 0; it is still a COVER closure (budget value 0) and is never trimmed.
- No stored close for the score date: approval written and APPROVED, executor skips with
  `no_close` (logged warning); re-planned the next night.
- Fractional book quantity (e.g. -12.4): COVER for 12.4; the executor orders whole shares
  (12). Alpaca does not allow fractional shorts, so this cannot arise from the broker book.
- Short to long in one night: one BUY row for the full delta (cover plus open), not HOLD and
  not two rows.
- Same name twice in `current`: ValueError (AC6); in `target`: pandas ValueError.

## Rollout
1. Land before the first nightly run on the packet 57-60 code (2026-11-05 22:00 UTC).
2. No schema or config change; revert is the one-line `map_action` change.

## Verify in paper trading
- When a held short leaves the target, Layer 4's trade list shows `COVER` with target 0 and
  the full quantity, pre-trade logs `CLOSING trade`, and Layer 6 logs `Submitted COVER`.
- Read-only: `SELECT ticker, action, current_shares, target_shares, status FROM
  position_approvals WHERE rebalance_date = :d AND action = 'COVER' AND target_shares = 0`.

## Test guidance
- AC1 -> `tests/test_short_cover_target_zero.py::TestMapAction::test_short_to_zero_is_cover`,
  `TestGenerateTrades::test_short_absent_from_target_covered_in_full`,
  `test_short_with_zero_target_row_covered`,
  `TestAdversarial::test_sub_share_target_rounds_to_full_cover`,
  `tests/test_rebalance.py::TestBuildUpAllowance::test_short_dropped_from_target_is_covered`
- AC2 -> `TestMapAction::test_long_to_zero_is_sell`,
  `TestGenerateTrades::test_long_absent_from_target_sold`
- AC3 -> `TestMapAction::test_other_cases_unchanged`, existing `tests/test_rebalance.py`
- AC4 -> `TestDownstream::test_executor_submits_buy_to_close`
- AC5 -> `TestDownstream::test_pre_trade_approves_as_closing_under_halt`,
  `TestTurnover::test_cover_to_zero_never_trimmed`
- AC6 -> `TestAdversarial::test_duplicate_current_ticker_raises`

## Decision log
- 2026-11-05: Fix by widening the COVER branch to `tgt <= 0` rather than adding a separate
  `tgt == 0` branch: it mirrors the SELL branch exactly and keeps one place that decides
  "buy back part or all of a short".
- 2026-11-05: Duplicate `current` tickers raise instead of being summed: two rows for one
  ticker mean the book is inconsistent, and summing would hide that.
