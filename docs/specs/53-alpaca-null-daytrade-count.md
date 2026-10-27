# Alpaca Null Day-Trade Count

**Spec file:** `docs/specs/53-alpaca-null-daytrade-count.md`
**Spec slug:** alpaca-null-daytrade-count
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-27

## Problem statement
The 2026-10-26 nightly run failed at the Book sync step (first nightly run of packet 44):
`execution/broker.py` `get_account` does `int(acct.daytrade_count)`, and Alpaca now returns
`daytrade_count` (and `pattern_day_trader`) as null for the paper account (alpaca-py 0.44.0,
reproduced read-only on 2026-10-27). Every Alpaca mode of `run_execution.py` calls
`get_account` first, so `--sync`, `--dry-run`, `--execute`, `--status` and `--cancel-pending`
all crash. No positions were synced and no `portfolio_nav` row was recorded for 2026-10-26; a
run without `--whatif` stops at Book sync.

## Scope
In scope:
- `execution/broker.py` `get_account`: stop reading `daytrade_count`; the returned dict drops
  the `day_trade_count` key.
- Tests for `get_account`, and the `get_account` stub in `tests/test_run_execution.py`.

Out of scope / non-goals:
- Pattern-day-trader handling of any kind (no code uses it today).
- Defaults for other account fields: equity and buying power must stay strict.
- Changing how run_all reacts to a failed Book sync.

## Assumptions
- Nothing reads `day_trade_count`: the only references are `get_account` itself and a test stub
  (`grep -rn day_trade`); `run_execution.py` logs only equity and buying power.
- `execution/book_sync.py` reads equity straight from `client.get_account()` and is unaffected.

## Proposed behavior / API
### Public interface
- `get_account(client) -> dict` returns `equity`, `buying_power` and `status`.

### Inputs / outputs
- A null or missing `daytrade_count` no longer raises.
- A null `equity` or `buying_power` still raises `TypeError`, as before.

## Acceptance criteria
- AC1: `get_account` returns equity, buying power and status when Alpaca's `daytrade_count`
  is null or missing.
- AC2: `get_account` no longer returns `day_trade_count`.
- AC3: A null `equity` or `buying_power` still raises, so a broken account read is not hidden.
- AC4: `run_execution.py --dry-run` connects to the paper account and runs the dry book sync.

## Security considerations
- Input handling: one fewer field read from the broker response; no new parsing.
- Auth/authz, secrets, data exposure, file system, network: no change.
- Dependency impact: none.
- Security notes for reviewers/testers: the fix removes a field instead of defaulting it, so
  no fake value enters the system.

## Edge cases
- `daytrade_count` null (seen in production), missing, or a real number: all accepted, never
  returned.
- `pattern_day_trader` null: never read.
- `equity` or `buying_power` null: still an error.

## Test guidance
- AC1 -> `tests/test_execution_broker.py::TestGetAccount::test_null_daytrade_count_is_accepted`,
  `test_missing_daytrade_attribute_is_accepted`
- AC2 -> `TestGetAccount::test_day_trade_count_not_returned`
- AC3 -> `TestGetAccount::test_null_equity_still_fails`, `test_null_buying_power_still_fails`
- AC4 -> manual: `python run_execution.py --dry-run --date 2026-10-26` against the paper account

## Decision log
- 2026-10-27: Remove the unused field rather than default a null to 0: a 0 would be a made-up
  value, and nothing needs the count.
