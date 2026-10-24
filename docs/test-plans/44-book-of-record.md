# Test Plan: book-of-record

Path: `docs/test-plans/44-book-of-record.md`

## What changed
- New `execution/symbols.py` and `execution/book_sync.py` (`sync_book`).
- `execution/broker.py`: `reconcile_positions` removed; broker positions keyed by db ticker.
- `execution/executor.py`: Alpaca symbols; no `portfolio_positions` writes.
- `execution/short_check.py`: Alpaca symbol for `get_asset`.
- `run_execution.py`: `--sync` and the pre/post-execution syncs use `sync_book`.
- `run_all.py`: "Book sync" step between Layers 3 and 4.
- `risk/pre_trade.py`: proposed-book pro-forma (`_target_book`), self-exclusion in check 8.
- `portfolio/rebalance.py` (`_update_positions` removed), `portfolio/state.py`
  (`save_positions` removed), `reporting/tear_sheet.py` (sector net = long + short).
- Tests: new `test_book_sync.py`, `test_symbols.py`, `test_tear_sheet.py`; updated
  `test_execution_broker.py` (reconcile tests removed), `test_state.py` (save tests removed),
  `test_execution_executor.py`, `test_execution_short_check.py`, `test_rebalance.py`,
  `test_run_all.py`.

## Acceptance criteria coverage
- AC1: `tests/test_book_sync.py::TestSyncBook::test_positions_match_broker`,
  `test_short_is_signed`, `test_stale_rows_removed`, `test_metadata_filled`,
  `test_missing_price_falls_back_to_close`
- AC2: `TestSyncBook::test_history_is_whole_book`, `test_resync_same_date_replaces_history`,
  `test_other_dates_history_kept`
- AC3: `TestSyncBook::test_entry_date_kept_for_held_position`, `test_entry_date_reset_on_flip`
- AC4: `TestSyncBook::test_dry_run_writes_nothing`
- AC5: `tests/test_rebalance.py::TestCommit::test_commit_writes_approvals_only`
- AC6: `tests/test_execution_executor.py::TestBookOfRecord::test_order_uses_alpaca_symbol`,
  `test_closing_guard_matches_alpaca_symbol`,
  `test_stale_close_skipped_without_touching_positions`,
  `TestExecuteApprovalsFilled::test_filled_order_leaves_positions_to_book_sync`,
  `tests/test_execution_short_check.py::test_uses_alpaca_symbol`,
  `tests/test_execution_broker.py::TestGetBrokerPositions::test_alpaca_symbol_mapped_to_db_ticker`,
  `tests/test_symbols.py::test_round_trip`
- AC7: `tests/test_run_all.py::test_book_sync_runs_between_layers_3_and_4`,
  `test_book_sync_command_and_whatif`, `test_book_sync_skipped_without_execution`,
  `test_book_sync_failure_stops_real_run`, `test_book_sync_failure_continues_whatif`
- AC8: `tests/test_tear_sheet.py::test_sector_net_weight_signed`
- AC9: `tests/test_risk_pre_trade.py::TestTargetBook::test_net_check_sees_whole_proposed_book`,
  `test_offsetting_shorts_count_toward_net`, `test_new_position_not_correlated_with_itself`,
  `test_null_position_weight_does_not_crash`

## Edge cases
- From spec: empty broker book (`test_stale_rows_removed`); missing current price
  (`test_missing_price_falls_back_to_close`); flipped direction (`test_entry_date_reset_on_flip`);
  zero equity (`test_zero_equity_leaves_weight_null`); other share classes (`BF-B` in
  `test_round_trip`).
- Additional adversarial cases:
  - Alpaca reports a short qty already signed: shares stay negative, not flipped twice
    (`test_negative_qty_from_api_still_one_sign`)
  - Alpaca fails mid-sync: the previous book is untouched (`test_broker_error_keeps_previous_book`)
  - A database write fails after the delete: rolled back, previous book intact
    (`test_write_failure_rolls_back`)
  - Broker holds `BRK.B`; a SELL of `BRK-B` is not skipped as stale
    (`test_closing_guard_matches_alpaca_symbol`)
  - A filled short no longer deletes the short position
    (`test_filled_short_does_not_delete_position`)
  - A COVER is not skipped when Alpaca reports the short's qty unsigned
    (`test_cover_guard_reads_side_when_qty_unsigned`)
  - A new position is not rejected for correlating with itself in the proposed book
    (`test_new_position_not_correlated_with_itself`)
  - A synced position with a NULL weight does not crash pre-trade
    (`test_null_position_weight_does_not_crash`)

## Notes
- Mutation checks: using the broker book instead of the proposed book fails
  `test_net_check_sees_whole_proposed_book`; dropping the self-exclusion fails
  `test_new_position_not_correlated_with_itself` and the existing
  `test_approved_in_normal_conditions`.
- Not unit-tested: `run_execution.py`'s order of sync, execute, summary, sync (thin CLI glue;
  covered by the reviewer's read and the dry-run sync below).
- Against `main`'s source, 12 of the updated tests fail (executor writes, symbols, rebalance
  commit, run_all step, tear sheet); `test_book_sync.py` and `test_symbols.py` cannot import.
- Dry-run `sync_book` against the real paper account and production database on 2026-10-24:
  `{'positions': 0, 'added': [], 'removed': [], 'changed': []}` (account flat, $100,000).
- The audit's end-to-end Verify for C10 and H5 (a paper rebalance, execute and sync) waits for
  packet 49's order handling.

## Commands
```bash
make lint
make test
make security
```
