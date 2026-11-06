# Test Plan: execute-button

Path: `docs/test-plans/62-execute-button.md`
Spec: `docs/specs/62-execute-button.md`

## What changed
- New `dashboard/execute_approvals.py`: latest rebalance date, pending and stale rows, approve,
  command, run, orders since the click, outcome.
- `dashboard/page_research.py`: `_approval_section` uses it and reports the outcome
  (`_approve_and_execute`); `_run_veto_checks` reads rows by column name.
- `execution/executor.py`: `already_submitted` skip from `execution_orders`.
- `run_execution.py`: `--execute` / `--dry-run` under `cache/execution.lock`; body moved to
  `_execute` unchanged.
- Tests: new `tests/test_execute_button.py`; additions to `tests/test_execution_executor.py`
  (`TestAlreadySubmitted`) and `tests/test_run_execution.py`.

## Acceptance criteria coverage
- AC1: `tests/test_execute_button.py::test_command_is_execute_with_date`,
  `test_click_runs_execute_for_latest_rebalance_date`, `test_command_refuses_non_iso_date`
- AC2: `test_stale_pending_rows_never_approved_or_run`, `test_only_stale_pending_shows_no_button`,
  `test_trimmed_and_rejected_rows_untouched`, `test_vetoed_rows_stay_pending`
- AC3: `test_success_only_when_every_approved_ticker_submitted`, `test_outcome_levels`,
  `test_orders_before_click_not_counted`
- AC4: `test_nonzero_exit_is_error_with_stderr`, `test_no_close_for_date_is_error`,
  `test_failure_after_some_orders_lists_them`
- AC5: `test_exit_zero_with_no_orders_is_warning`,
  `test_second_click_while_orders_open_is_not_success`,
  `test_partial_submission_names_failed_and_missing`
- AC6: `test_success_only_when_every_approved_ticker_submitted` (table columns and rows),
  `test_failure_after_some_orders_lists_them`
- AC7: `tests/test_execution_executor.py::TestAlreadySubmitted::test_prior_order_blocks_resubmission`
  (PENDING, PARTIAL, FILLED, CANCELLED, FAILED), `test_second_run_same_date_submits_nothing`,
  `test_duplicate_approvals_in_one_run_submit_once`, `test_other_action_or_date_does_not_block`
- AC8: `tests/test_run_execution.py::test_execute_refused_while_lock_held`,
  `test_lock_released_after_run`
- AC9: `TestAlreadySubmitted::test_dry_run_rows_do_not_block`
- AC10: `tests/test_execute_button.py::test_veto_keys_are_tickers`, `test_vetoed_rows_stay_pending`

## Edge cases
- From spec:
  - No close for D on every row: Layer 6 exits 1; page shows the error
    (`test_no_close_for_date_is_error`; exit code from existing
    `tests/test_run_execution.py::test_all_no_close_fails_the_run`)
  - Run fails after approving: error plus rerun hint, orders recorded so far listed
    (`test_failure_after_some_orders_lists_them`)
- Adversarial (beyond the spec edge cases):
  - Script exits non-zero (`test_nonzero_exit_is_error_with_stderr`)
  - Script exits 0 with zero orders, the original bug (`test_exit_zero_with_no_orders_is_warning`)
  - Second click while orders are open (`test_second_click_while_orders_open_is_not_success`;
    executor side `test_prior_order_blocks_resubmission[PENDING]`)
  - Click with no close for the score date (`test_no_close_for_date_is_error`)
  - Earlier order already filled, so not in Alpaca's open list
    (`test_prior_order_blocks_resubmission[FILLED]`)
  - Stale PENDING rows from 2026-05-21, as in the live DB
    (`test_stale_pending_rows_never_approved_or_run`)
  - Concurrent run holds the lock (`test_execute_refused_while_lock_held`)
  - Shell metacharacters in the date (`test_command_refuses_non_iso_date`)

## Notes
- No network: `subprocess.run` is patched in `dashboard.execute_approvals`; the Alpaca client is
  a `MagicMock`; Streamlit is a `MagicMock` with the button returning True.
- `_run_veto_checks` is patched in page tests (its market-hours check reads the wall clock);
  `test_veto_keys_are_tickers` exercises it through the halt-lock path in a temp directory.
- The lock tests write `cache/execution.lock` in the repository's `cache/` (gitignored).

## Commands
```bash
make lint
make test
make security
```
