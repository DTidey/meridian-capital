## Summary
- Implements Layer 6, which reads APPROVED rows from `position_approvals` and routes them to Alpaca as day limit orders with a 0.5% slippage buffer, chunking large orders to stay within 2% of 20-day ADV.
- Adds shortability checking with a 7-day file-backed cache per ticker; SHORT orders are skipped with a WARNING when the ticker is not easy-to-borrow.
- Adds position reconciliation that diffs live broker positions against the portfolio model and auto-corrects discrepancies greater than 0.5 shares.
- Records fill data and slippage in basis points per order and exposes 30-day slippage statistics; a SIGINT handler cancels all open Alpaca orders and exits gracefully.

## Spec
- Spec: `docs/specs/06-order-execution.md`
- Test plan: `docs/test-plans/06-order-execution.md`
- PR draft path: `.ai/pr-description/06-order-execution.md`

## Acceptance Criteria
- [x] AC1: The system reads only rows with `status=APPROVED` from `position_approvals` for the given `rebalance_date` and does not process PENDING or REJECTED rows.
- [x] AC2: All orders are submitted as day limit orders with a 0.5% slippage buffer above the current price for BUY/COVER actions and 0.5% below for SELL/SHORT actions.
- [x] AC3: Orders where the absolute share quantity divided by 20-day ADV exceeds 2% are split into chunks of 2% of ADV to limit market impact.
- [x] AC4: SHORT actions are skipped with a WARNING log entry (not an error) if `short_check.is_shortable()` returns False for the ticker; shortability results are cached for 7 days per ticker.
- [x] AC5: Partial fills are kept without retry; only zero-fill orders are logged as WARNING and marked CANCELLED in `execution_orders`.
- [x] AC6: `reconcile_positions()` diffs broker positions against `portfolio_positions` and auto-corrects any discrepancy greater than 0.5 shares by updating `portfolio_positions` to match the broker, logging a WARNING for each correction.
- [x] AC7: A SIGINT signal during execution cancels all open Alpaca orders via `cancel_open_orders()` and exits gracefully without raising an exception.
- [x] AC8: `run_execution.py --dry-run` logs the orders that would be submitted but makes no Alpaca API calls and writes no rows to `execution_orders`.
- [x] AC9: Slippage is recorded in basis points per filled order and `slippage_stats()` returns mean, p95, worst ticker, and count over a configurable trailing window.
- [x] AC10: The `ALPACA_PAPER` environment variable controls whether orders are routed to the paper or live endpoint; it defaults to `true` and is never hardcoded.

## Security Review
- [x] Security considerations were reviewed and updated in the linked spec
- [x] No meaningful security impact beyond API key handling via environment variables

## Validation
- [x] `make lint`
- [x] `make test`
- [x] `make security`

## GitHub Checks
- Required checks for `main`:
  - `CI / test`
  - `CodeQL / analyze`

## Changelog
- [x] Add to `CHANGELOG.md` under `## Unreleased`

## Open Risks
- None. All tests passing. Code is read-only with respect to external systems during tests.
