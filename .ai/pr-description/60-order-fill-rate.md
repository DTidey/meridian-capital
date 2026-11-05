## Summary

Queued DAY limits at the score date's close +/- 0.5% missed whenever the next session gapped
more than 0.5% against them: 10-27 to 11-03, 43 of 65 orders filled (~71% of value). Of the
13 expiries that were market misses (the rest were owner cancellations and bugs since fixed),
8 needed a band wider than 0.5%.

- `config.yaml`: `execution.limit_slippage_pct` 0.005 -> 0.015 (owner decision); order type,
  time in force and schedule unchanged (queued DAY limits, the owner's 2026-10-24 choice).
- `execution/executor.py`: `_limit_band` refuses a band that is not a number in (0, 0.05] at
  the start of `execute_approvals`, before approvals are read or Alpaca is called;
  `_limit_price` defaults to 1.5%.
- No explicit roll-forward of unfilled orders: Layer 4 re-plans every night, and with packet
  57 the budget no longer drops a returning build-up trade.
- Specs 06 (AC2, limit section) and 49 (decision log) updated.

## Spec

- Spec: `docs/specs/60-order-fill-rate.md`
- Test plan: `docs/test-plans/60-order-fill-rate.md`
- PR draft path: `.ai/pr-description/60-order-fill-rate.md`

## Acceptance Criteria

- [x] AC1: BUY/COVER limits at close x 1.015, SELL/SHORT at close x 0.985 (score date's close).
- [x] AC2: Band read from config each run; 0.005 reproduces the old limits; nothing else changes.
- [x] AC3: An approval with no stored close is still skipped.
- [x] AC4: Config comment, spec 06 AC2 and the packet 49 note state the band and its basis.
- [x] AC5: A band outside (0, 0.05] stops Layer 6 with an error naming the key, before any order.

## Security Review

- [x] Security considerations reviewed in `docs/specs/60-order-fill-rate.md`
- [x] Input validation changed: the band is range-checked (a typo such as 0.15 can no longer
  allow 15% fills)
- Reviewer focus: the check runs before any broker call (test
  `test_validation_before_alpaca_calls`).

## Validation

- 13 new tests failed before the change and pass after (`TestLimitBand`).
- The 5 orders that traded through their limit without filling were checked with read-only
  Alpaca GETs (order records, 1-minute bars): all were valid DAY limits that touched the limit
  only in the opening minute(s); Alpaca's paper engine fills on marketable quotes, not prints.
  Not a code fault. At 1.5%, 3 of the 5 had marketable minutes later in the session.
- Expected effect (from next-session minute and daily ranges, so an estimate): of the 56 orders
  10-27 to 11-03 that were not owner-cancelled or bug-rejected, 43 filled (77%); at 1.5% about
  51 (~91%).
- `make lint`: pass. `make test`: pass (counts in the phase-2 summary).

## GitHub Checks

- Required checks for `main`: `CI / test`, `CodeQL / analyze`

## Changelog

- [x] `CHANGELOG.md` under `## Unreleased`.

## Open Risks

- A filled order can cost up to 1.5% versus the close instead of 0.5%; on gap days this is
  roughly what re-planning the next night would pay.
- Slippage is still measured against the limit (backlog), so the wider band makes the
  dashboard's slippage figures look better, not worse; read them against the close.
