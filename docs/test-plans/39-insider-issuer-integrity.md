# Test Plan: insider-issuer-integrity

Path: `docs/test-plans/39-insider-issuer-integrity.md`

## What changed
- `data/sec_data.py`:
  - `_load_cik_map` reads `company_tickers.json` once per run; `_ticker_to_cik`
    (`browse-edgar`) and stored-URL CIK reuse removed.
  - `_parse_form4_xml` returns `(issuer_cik, transactions)`, each with `txn_index`.
  - Form 4 transactions stored only when the issuer CIK is the ticker's CIK.
  - `rebuild_insider_transactions` rebuilds rows and cluster flags from stored XML in one
    transaction; `_write_cluster_flags` is `_flag_cluster_buys` without the commit.
- `data/db.py`: `insider_transactions.txn_index`; unique key `(ticker, accession_no, txn_index)`;
  `_migrate_insider_unique_key` in `initialise_schema`.
- `run_data.py --rebuild-insider`; `run_all.py --forms` passthrough.
- Tests: `tests/test_sec_data.py`, `tests/test_db.py`, new `tests/test_run_data_args.py` and
  `tests/test_run_all.py`. Existing parser tests unpack the new tuple; the old unique-key test
  now keys on `txn_index`.

## Acceptance criteria coverage
- AC1: `tests/test_sec_data.py::TestCikMap` (`test_maps_tickers_and_strips_zeros`,
  `test_unexpected_json_raises`, `test_invalid_json_raises`, `test_http_error_raises`)
- AC2: `TestUpdateSecDataCik::test_mapped_cik_wins_over_stored_url`,
  `test_no_warning_when_cik_unchanged`
- AC3: `TestUpdateSecDataCik::test_ticker_missing_from_map_skipped`,
  `test_map_failure_stores_nothing`, `test_never_calls_browse_edgar`
- AC4: `TestParseForm4Xml::test_issuer_cik_and_txn_index`, `test_leading_zeros_stripped`,
  `test_missing_issuer_cik_is_none`
- AC5: `TestUpdateSecDataIssuer::test_other_issuer_not_stored` (filing row kept, no insider rows,
  INFO total), `test_same_issuer_stored`
- AC6: `TestUpdateSecDataIssuer::test_same_shares_both_stored` (M and S of 43,778);
  `tests/test_db.py::TestInsiderUniqueMigration::test_same_shares_different_index_both_stored`
- AC7: `tests/test_db.py::TestInsiderUniqueMigration`: `test_fresh_schema_has_new_key`,
  `test_rerun_succeeds_and_changes_nothing` (SQLite); `test_pg_replaces_old_key`,
  `test_pg_second_run_changes_nothing` (PostgreSQL DDL path against a simulated catalog holding
  the production name `insider_transactions_ticker_accession_no_insider_name_date__key`)
- AC8: `TestRebuildInsider::test_rebuild_replaces_rows` (other-issuer excluded),
  `test_share_classes_both_receive` (GOOG and GOOGL), `test_stale_flags_removed_and_recomputed`,
  `test_logs_counts`; `TestRebuildPredecessorCik::test_configured_predecessor_kept` (XOM's
  34088), `test_unconfigured_cik_with_matching_symbol_excluded` (the fund trading as "BNY"),
  `test_predecessor_outside_universe_excluded`, `test_shipped_config_lists_xom_predecessor`
- AC9: `TestRebuildInsider::test_failure_leaves_data_unchanged` (cluster step raises after the
  delete and inserts), `test_map_failure_leaves_data_unchanged`
- AC10: `tests/test_run_data_args.py::test_flag_parsed`, `test_rebuild_runs_only_the_rebuild`,
  `test_without_flag_rebuild_not_called`; `tests/test_run_all.py::test_forms_forwarded`,
  `test_several_forms_forwarded`, `test_no_forms_not_forwarded`
- AC11: `make lint`, `make test`, `make security`

## Edge cases
- From spec:
  - Map entries for tickers outside the universe ignored (`test_renamed_ticker_mapped_by_issuer`:
    the map also holds `BK`, which gets no rows)
  - Universe ticker missing from the map: warned and skipped
    (`test_ticker_missing_from_map_skipped`, `TestRebuildInsider::test_unmapped_universe_ticker`)
  - Form 4 with no `issuerCik`: no rows (`test_missing_issuer_not_stored`)
  - Only derivative transactions: no rows (`test_derivative_only_has_no_rows`)
  - Issuer CIK with leading zeros (`test_leading_zeros_stripped`, `test_same_issuer_stored`
    uses `0000320193`)
  - Share-class sibling whose filing row is owned by the other class
    (`test_share_classes_both_receive`)
  - Same accession processed again: unique key makes the insert a no-op
    (`tests/test_db.py::test_insider_transactions_unique_constraint`,
    `test_rebuild_is_repeatable`)
- Additional adversarial cases:
  - CIK values that could reach a URL path (`320193/../../x`, full-width digits, sign, `0`,
    booleans) make the map load fail (`TestCikMap::test_non_digit_cik_raises`), and
    `update_sec_data` then makes no further request
    (`TestUpdateSecDataCik::test_map_with_path_in_cik_stores_nothing`)
  - Non-numeric `issuerCik` in the XML is not trusted (`test_non_digit_issuer_cik_is_none`)
  - Issuer CIK that only shares a prefix with the ticker's CIK is a mismatch
    (`test_prefix_cik_is_mismatch`)
  - Two lines identical in every field, code included (NVDA two S lots), both stored
    (`test_identical_lines_both_stored`)
  - Rebuild run twice gives identical rows and flags (`test_rebuild_is_repeatable`)
  - Malformed stored XML is counted as skipped, not fatal (`test_malformed_xml_skipped`)
  - Renamed ticker's filings (BK) reach the current ticker (BNY) by issuer CIK, and the old
    ticker gets nothing (`test_renamed_ticker_mapped_by_issuer`)
  - Old 5-column key under another name is found by its columns, and unrelated unique keys are
    left alone (`test_pg_old_key_found_by_columns_not_name`, `test_pg_other_keys_untouched`)
  - `--rebuild-insider --tickers` is rejected before any database work, because the rebuild
    deletes every insider row (`test_rebuild_with_tickers_rejected`)

## Notes
- Determinism: SEC is a fake session routed by URL; the rate limiter interval is set to 0;
  dates are relative to `utc_now()`. No network, no production database.
- The PostgreSQL migration is tested against a simulated catalog, not a live server; the SQL
  itself is reviewed against the production constraint name. SQLite tables are created fresh
  from metadata; an existing SQLite table would keep its old constraint (SQLite cannot drop
  one in place). Production is PostgreSQL only.
- Before the fix the new tests failed (import errors for `_load_cik_map`,
  `rebuild_insider_transactions`, `_migrate_insider_unique_key`; missing `--rebuild-insider`
  and `--forms`).

## Commands
```bash
make lint
make test
make security
```
