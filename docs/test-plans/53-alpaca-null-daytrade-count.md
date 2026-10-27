# Test Plan: alpaca-null-daytrade-count

Path: `docs/test-plans/53-alpaca-null-daytrade-count.md`

## What changed
- `execution/broker.py`: `get_account` no longer reads or returns the day-trade count.
- `tests/test_execution_broker.py`: `TestGetAccount`.
- `tests/test_run_execution.py`: the `get_account` stub drops `day_trade_count`.

## Acceptance criteria coverage
- AC1: `TestGetAccount::test_null_daytrade_count_is_accepted`,
  `TestGetAccount::test_missing_daytrade_attribute_is_accepted`
- AC2: `TestGetAccount::test_day_trade_count_not_returned`
- AC3: `TestGetAccount::test_null_equity_still_fails`,
  `TestGetAccount::test_null_buying_power_still_fails`
- AC4: manual, `python run_execution.py --dry-run --date 2026-10-26` against the paper account
  (no APPROVED rows that date, so no orders; dry book sync writes nothing)

## Edge cases
- From spec: null, missing and numeric `daytrade_count`; null `pattern_day_trader`.
- Additional adversarial cases:
  - `daytrade_count` attribute absent altogether, not just null
    (`test_missing_daytrade_attribute_is_accepted`)
  - A real count of 3 is still not returned (`test_day_trade_count_not_returned`)
  - Null equity and null buying power still raise (`test_null_equity_still_fails`,
    `test_null_buying_power_still_fails`)

## Notes
- The three AC1/AC2 tests fail on `main` (`TypeError` / `AttributeError` / key present); the
  AC3 tests pass on both, guarding against an over-broad fix.

## Commands
```bash
make lint
make test
make security
```
