## Summary

Fixes the Book sync failure in the 2026-10-26 nightly run. Alpaca now returns
`daytrade_count` as null for the paper account, and `get_account` did
`int(acct.daytrade_count)`, so every Alpaca mode of `run_execution.py` crashed before doing
anything. No positions were synced and no NAV row was recorded; a non-whatif run would stop at
Book sync.

- `get_account` returns equity, buying power and status only. Nothing read the day-trade
  count, so it is removed rather than defaulted.
- Null equity or buying power still raises.

## Spec

- Spec: `docs/specs/53-alpaca-null-daytrade-count.md`
- Test plan: `docs/test-plans/53-alpaca-null-daytrade-count.md`
- PR draft path: `.ai/pr-description/53-alpaca-null-daytrade-count.md`

## Acceptance Criteria

- [x] AC1: `get_account` works when `daytrade_count` is null or missing.
- [x] AC2: `get_account` no longer returns `day_trade_count`.
- [x] AC3: Null equity or buying power still raises.
- [x] AC4: `run_execution.py --dry-run` connects to the paper account and runs the dry book sync.

## Security Review

- [x] Security considerations reviewed in `docs/specs/53-alpaca-null-daytrade-count.md` (one
  fewer broker field read; no new input, secrets or network change)

## Validation

- New `TestGetAccount` tests: three fail on `main`, all pass here
- `make lint`: pass
- `make test`: 1533 passed
- `make security`: pass ("No known vulnerabilities found")
- `python run_execution.py --dry-run --date 2026-10-26` against the paper account: connected
  (equity 100000.00), order sync and dry book sync ran, no orders

## Risks and follow-ups

- After merge, pull into the live copy. Running `run_execution.py --sync` once by hand records
  the first `portfolio_nav` row; otherwise tonight's nightly does it.
