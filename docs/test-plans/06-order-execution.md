# Test Plan: order-execution

Path: `docs/test-plans/06-order-execution.md`

## What changed
- Initial implementation of Layer 6 Order Execution. All code shipped in the founding commit.

## Acceptance criteria coverage
- AC1: `tests/test_execution_executor.py` — fixture with mixed APPROVED/PENDING/REJECTED rows; asserts only APPROVED rows are fetched and processed; PENDING and REJECTED rows produce no `execution_orders` inserts.
- AC2: `tests/test_execution_executor.py` — `_limit_price` unit tests: BUY/COVER with known price asserts result equals `price * 1.005`; SELL/SHORT asserts `price * 0.995`; parametrised across all four action types.
- AC3: `tests/test_execution_executor.py` — `_chunk_orders` with ADV mock: shares equal to 3% of ADV splits into two chunks; shares equal to 1.9% of ADV produces a single chunk; boundary at exactly 2% of ADV produces a single chunk.
- AC4: `tests/test_execution_short_check.py` — `is_shortable()` returns False when `asset.shortable=False`; cache-hit test: second call with same ticker does not invoke `client.get_asset()` again; cache-miss test: expired TTL triggers fresh API call; any API error returns False (safe default).
- AC5: `tests/test_execution_executor.py` — partial fill kept without retry: `filled_qty < ordered_qty > 0` leaves row with status PARTIAL and no second `submit_order` call; zero-fill test: `filled_qty == 0` logs WARNING and sets status CANCELLED in `execution_orders`.
- AC6: `tests/test_execution_broker.py` — `reconcile_positions()` with broker qty and DB qty differing by 0.6 shares: `portfolio_positions` updated to broker value, WARNING logged, correction dict returned; difference of 0.4 shares: no correction, empty list returned.
- AC7: `tests/test_execution_order_manager.py` — SIGINT handler test: signal raised during execution calls `cancel_open_orders(client)`; `cancel_open_orders` count matches number of open orders returned by mock; no exception propagates out of the `OrderManager` context.
- AC8: `tests/test_execution_executor.py` — `submit_order` with `dry_run=True` returns None; no row inserted into `execution_orders`; no method on the mock Alpaca client that places an order is called.
- AC9: `tests/test_execution_costs.py` — `compute_slippage` bps formula: known ordered/filled prices and side produce expected signed bps value; `slippage_stats` aggregation: fixture with 5 FILLED rows returns correct mean, p95, worst_ticker, and count.
- AC10: `tests/test_execution_broker.py` — `ALPACA_PAPER=true` routes to paper endpoint URL; `ALPACA_PAPER=false` routes to live endpoint URL; default value when env var absent is `true`.

## Edge cases
- From spec:
  - SHORT actions are skipped with WARNING (not an error) if `is_shortable()` returns False; execution continues for remaining approvals.
  - Partial fills are kept without retry; only zero-fill orders trigger CANCELLED status.
  - `market_is_open()` returns False: logs WARNING but does not block execution.
  - `ALPACA_PAPER` defaults to `true`; absent `ALPACA_API_KEY` or `ALPACA_SECRET_KEY` raises on startup, not at import time.
  - Shortability cache TTL is 7 days; stale cache entry triggers fresh `client.get_asset()` call.
- Additional adversarial cases:
  - Alpaca `submit_order` raises a network exception mid-batch: the failed order is logged as FAILED, execution continues for remaining approvals without raising to the caller.
  - `_chunk_orders` with shares of exactly 0: returns an empty list without dividing by zero or producing a zero-size chunk.
  - `slippage_stats` called when `execution_orders` has no FILLED rows in the trailing window: returns zeroed dict (`mean_bps=0, p95_bps=0, worst_ticker=None, count=0`) without raising.
  - `reconcile_positions` called when broker returns a ticker not present in `portfolio_positions`: new ticker is added to `portfolio_positions` with broker quantity and logged as a WARNING correction.

## Notes
- Flaky risks: External API calls (Alpaca, shortability check) are mocked in tests; no network dependency.
- Determinism considerations: All tests use deterministic fixtures; slippage_stats percentile computed over fixed sets of bps values.
