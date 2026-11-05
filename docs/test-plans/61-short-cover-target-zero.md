# Test Plan: short-cover-target-zero

Path: `docs/test-plans/61-short-cover-target-zero.md`
Spec: `docs/specs/61-short-cover-target-zero.md`

## What changed
- `portfolio/rebalance.py` `map_action`: COVER branch is `tgt <= 0 and cur < 0 and delta > 0`.
- `portfolio/rebalance.py` `generate_trades`: ValueError on duplicate tickers in `current`.
- `tests/test_rebalance.py`: strict-xfail marker removed from
  `test_short_dropped_from_target_is_covered` (now passes).
- New `tests/test_short_cover_target_zero.py`.

## Acceptance criteria coverage
- AC1: `tests/test_short_cover_target_zero.py::TestMapAction::test_short_to_zero_is_cover`,
  `TestGenerateTrades::test_short_absent_from_target_covered_in_full`,
  `TestGenerateTrades::test_short_with_zero_target_row_covered`,
  `TestAdversarial::test_sub_share_target_rounds_to_full_cover`,
  `tests/test_rebalance.py::TestBuildUpAllowance::test_short_dropped_from_target_is_covered`
- AC2: `TestMapAction::test_long_to_zero_is_sell`,
  `TestGenerateTrades::test_long_absent_from_target_sold`
- AC3: `TestMapAction::test_other_cases_unchanged` (11 cases: open/add/reduce on both sides,
  short to long, long to short, three HOLDs); all existing `tests/test_rebalance.py` tests
- AC4: `TestDownstream::test_executor_submits_buy_to_close` (generate_trades commit -> pre-trade
  -> execute_approvals with a mocked client: BUY, BUY_TO_CLOSE, qty 12, limit 40.60 from a
  40.00 close and the 1.5% band)
- AC5: `TestDownstream::test_pre_trade_approves_as_closing_under_halt`,
  `TestTurnover::test_cover_to_zero_never_trimmed` (budget binds on a long rotation; the
  cover keeps its full delta and priority 1)
- AC6: `TestAdversarial::test_duplicate_current_ticker_raises`

## Edge cases
- From spec:
  - Name left the scored universe, no Layer 4 price: still COVER, never trimmed
    (`TestTurnover::test_cover_to_zero_without_price_not_trimmed`)
  - No stored close for the score date: APPROVED but executor skips with `no_close`, no order
    (`TestDownstream::test_no_close_skipped_not_dropped`)
- Additional adversarial cases:
  - Fractional book quantity -12.4: COVER for the full 12.4
    (`TestAdversarial::test_fractional_short_covered_whole_shares`)
  - Sub-share target -0.4 rounds to 0: full cover, not a 0.4 residue
    (`TestAdversarial::test_sub_share_target_rounds_to_full_cover`)
  - Short to long in one night: exactly one row, BUY for the whole delta (15), not HOLD and
    not double-counted (`TestAdversarial::test_short_to_long_one_row_not_hold`)
  - Same name twice in `current` (ValueError, AC6) and in `target` (pandas ValueError)
    (`TestAdversarial::test_duplicate_target_ticker_raises`)

## Notes
- Flaky risks: none; Alpaca client mocked, SQLite temp DB.
- Determinism: assertions look rows up by ticker, not position (`generate_trades` iterates a
  set).

## Commands
```bash
make lint
make test
make security
```
