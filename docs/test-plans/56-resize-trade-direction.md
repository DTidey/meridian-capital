# Test Plan: resize-trade-direction

Path: `docs/test-plans/56-resize-trade-direction.md`

## What changed
- `portfolio/rebalance.py`: `target_shares` rounded to whole shares; `_map_action` renamed
  `map_action`.
- `risk/pre_trade.py`: blackout resize writes `action` from `map_action`.
- `risk/tail_risk.py`: reduce-gross writes `action` from `map_action`.
- `risk/circuit_breakers.py`: size-down writes `action` from `map_action`; HOLD rows are not
  loaded as opening trades.
- `execution/executor.py`: `_direction_skip_reason` (`no_change`, `action_mismatch`); a
  stored delta of 0.0 is no longer read as missing.

## Acceptance criteria coverage
- AC1: `tests/test_rebalance.py::TestWholeShareTargets::test_new_position_target_rounded`,
  `test_cover_target_rounded`
- AC2: `tests/test_risk_pre_trade.py::TestBlackoutResizeAction::test_resize_below_holding_sells`
- AC3: `TestBlackoutResizeAction::test_resize_to_holding_is_hold`
- AC4: `tests/test_risk_tail_risk.py::TestReduceGrossAction::test_cut_below_holding_sells`,
  `test_cut_to_holding_is_hold`
- AC5: `tests/test_execution_executor.py::TestTradeDirectionGuard::test_hold_skipped`,
  `test_zero_delta_skipped`
- AC6: `TestTradeDirectionGuard::test_action_mismatch_refused`
- AC7: `tests/test_risk_circuit_breakers.py::TestSizeDownAction::test_size_down_below_holding_sells`,
  `test_hold_row_not_sized_down`

## Edge cases
- From spec:
  - New position resized keeps its action
    (`TestBlackoutResizeAction::test_new_position_keeps_action`)
  - HOLD row from the blackout is not cut again by reduce-gross
    (`TestReduceGrossAction::test_hold_row_not_cut_again`)
- Additional adversarial cases:
  - Short-side blackout mirror: SHORT -14 -> -20 halved to -10 becomes COVER 4
    (`TestBlackoutResizeAction::test_short_resize_above_holding_covers`)
  - Long-side executor mirror: BUY with delta -1 refused
    (`TestTradeDirectionGuard::test_buy_with_negative_delta_refused`)
  - Sub-share delta (0.4) that would round to a qty 0 order is skipped
    (`TestTradeDirectionGuard::test_sub_share_delta_skipped`)
  - Sub-share target (0.4) becomes a clean full close
    (`TestWholeShareTargets::test_sub_share_target_is_full_close`)
  - A consistent COVER still submits the right whole-share quantity
    (`TestTradeDirectionGuard::test_consistent_cover_submitted`)
- Replay of the 2026-10-29 approval rows (manual, see PR draft).

## Notes
- Flaky risks: none; in-memory SQLite and mocked Alpaca client.
- Determinism considerations: Python `round` is half to even; tests avoid .5 targets.

## Commands
```bash
make lint
make test
make security
```
