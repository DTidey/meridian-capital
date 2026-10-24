# Test Plan: order-handling

Path: `docs/test-plans/49-order-handling.md`

## What changed
- `execution/executor.py`: `poll_order` removed; `execute_approvals` split into
  `_execute_one`, `_skip_reason`, `_submit_chunk`; limits from `_closes_on(score_date)`;
  `_open_order_tickers` guard; `submit_order` returns the Alpaca order.
- New `execution/order_sync.py`: `db_status`, `sync_orders`.
- `run_execution.py`: order sync before the book sync (`--sync`) and before executing.
- `config.yaml`: `poll_timeout_s`, `poll_interval_s` removed.
- Tests: new `test_order_sync.py`, `test_run_execution.py`; `test_execution_executor.py`
  reworked (old zero-fill CANCELLED test removed; approvals now store a close for the date).

## Acceptance criteria coverage
- AC1: `tests/test_execution_executor.py::TestSubmission::`
  `test_records_alpaca_status_without_polling`,
  `test_rejected_at_submission_is_failed`
- AC2: `TestSubmission::test_limit_from_close_for_score_date`, `test_no_close_for_date_skipped`
- AC3: `TestSubmission::test_open_order_skipped`, `test_open_orders_unavailable_raises`,
  `test_open_orders_listed_with_max_limit`, `test_truncated_open_orders_raises`
- AC4: `tests/test_order_sync.py::TestSyncOrders::test_filled_order_recorded_with_slippage`,
  `test_partial_then_expired_keeps_fill`, `test_still_open_unchanged`, `test_rejected_is_failed`,
  `test_final_rows_not_fetched`, `TestDbStatus::test_mapping`, `test_enum_value_accepted`
- AC5: `tests/test_run_execution.py::test_sync_orders_before_book`,
  `test_execute_syncs_orders_first`, `test_dry_run_syncs_orders_and_dry_book`
- AC6: `tests/test_order_sync.py::TestSyncOrders::test_fetch_error_leaves_row`,
  `test_malformed_order_leaves_row`
- AC7: `tests/test_run_execution.py::test_all_no_close_fails_the_run`,
  `test_some_no_close_still_succeeds`

## Edge cases
- From spec: rejection at submission (`test_rejected_at_submission_is_failed`); partial fill
  then expiry (`test_partial_then_expired_keeps_fill`); rows without an order id
  (`test_rows_without_order_id_skipped`); no close for the date (`test_no_close_for_date_skipped`);
  dry run (`test_dry_run_does_not_list_open_orders`).
- Additional adversarial cases:
  - A submission error is FAILED, never CANCELLED (`test_submit_error_is_failed_not_cancelled`)
  - A stale `current_price` and an older close do not set the limit
    (`test_limit_from_close_for_score_date`)
  - An open order held as `BRK.B` blocks a new `BRK-B` order (`test_open_order_skipped`)
  - A short filled below its limit counts as adverse slippage (`test_short_slippage_sign`)
  - `done_for_day` stays open, with a partial fill shown as PARTIAL (`test_mapping`)
  - Statuses that sound final but are not (`suspended`, `pending_cancel`, `stopped`,
    `calculated`, `held`) stay open (`test_mapping`)
  - A full page of 500 open orders is refused as possibly truncated
    (`test_truncated_open_orders_raises`)
  - An unparseable order value is isolated to its row (`test_malformed_order_leaves_row`)
  - A run where every approval lacks a close exits 1 (`test_all_no_close_fails_the_run`)
  - An unchanged PARTIAL row is not rewritten (`test_unchanged_partial_not_rewritten`)

## Notes
- Against `main`'s executor, `test_no_close_for_date_skipped`, `test_open_order_skipped` and
  `test_open_orders_unavailable_raises` fail; the polling tests would wait out `main`'s
  120-second poll per order, which is the defect. `test_order_sync.py` and
  `test_run_execution.py` cannot run on `main` (no `execution.order_sync`).

## Commands
```bash
make lint
make test
make security
```
