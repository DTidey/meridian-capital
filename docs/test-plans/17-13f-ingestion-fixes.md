# Test Plan: 13f-ingestion-fixes

Path: `docs/test-plans/17-13f-ingestion-fixes.md`

## What changed
- `data/institutional.py`: unscaled values with a per-filing $ thousands check; options and PRN
  rows skipped; rows summed per ticker; original 13F-HR per period only, stored by period with
  `filed_date`; infotable found via `index.json`; legacy rows purged; summary rebuilt per fund.
- `data/db.py`: `institutional_holdings.filed_date`, added to existing databases.
- `config.yaml`: Third Point and Appaloosa CIKs.
- `factors/loader.py`: institutional summary built from holdings filed by the score date.

## Acceptance criteria coverage
- AC1: `TestParseInfotableRules::test_value_is_whole_dollars`; updated
  `TestParseInfotable::test_parses_namespaced_xml`, `test_handles_comma_formatted_numbers`.
- AC2: `TestParseInfotableRules::test_skips_options`, `test_skips_principal_amounts`,
  `test_missing_amount_type_is_kept`.
- AC3: `TestAggregateByTicker` (4 tests); `TestStorage::test_stores_period_and_filed_date_aggregated`.
- AC4: `TestRecentAccessions` (5 tests).
- AC5: `TestStorage` (2 tests), `TestPurgeLegacyRows` (2 tests).
- AC6: `TestSummariseHoldings` (10 tests; `test_new_position_detected` now gives the fund a
  prior filing, per the first-filing rule).
- AC7: `TestInfotableFilenames` (5 tests).
- AC8: `TestConfigCiks`.
- AC9: `TestNormaliseValueUnits` (5 tests), `TestStorage::test_thousands_filing_rescaled_and_logged`.
- AC10: `TestLoaderPointInTime` (2 tests).

## Edge cases
- From spec:
  - First loaded filing: `test_first_filing_has_no_new_positions`
  - Skipped quarter: `test_skipped_quarter_compares_with_latest_earlier_filing`
  - Options-only ticker: `test_skips_options`
  - Not-yet-filed fund: `test_fund_not_yet_filed_carries_forward`
  - Full exit: `test_full_exit_row_written`
  - Index lists no infotable: `test_falls_back_when_index_lists_no_xml`
- Additional adversarial cases:
  - Path-like names in `index.json` rejected: `test_rejects_path_like_names`
  - Malformed `index.json`: `test_malformed_index_falls_back`
  - Blank `<putCall>` is not an option: `test_blank_put_call_is_common_stock`
  - Upper-case `CALL` in a namespaced filing: `test_put_call_case_insensitive_namespaced`
  - 13F-HR without `reportDate`: `test_missing_report_date_skipped`
  - One odd row can't rescale a filing: `test_one_odd_row_does_not_flip_filing`
  - None/zero shares and values: `test_rows_without_shares_or_value_ignored`
  - Duplicate filings for one period: `test_duplicate_period_keeps_most_recent_filing`
  - SEC list order reversed: `test_order_enforced_not_assumed`
  - `?`, `#` and `\` in index filenames: `test_rejects_query_fragment_and_backslash`
  - Score date between quarter end and filing: `test_filings_after_cutoff_excluded`

## Notes
- Flaky risks: none in the suite (SEC calls are faked).
- Live dry run (2026-10-14) into a throwaway SQLite database with the production universe:
  all 9 funds loaded 6 periods each (Pershing Square's latest is a 13F-NT, so it starts a
  quarter earlier); Baupost's 6 filings rescaled from $ thousands; lowest implied price per
  fund $3.49 to $40.34; Berkshire AAPL Q2 2026 = 227,917,808 shares, $65.95B; Point72 has no
  AAPL row (it holds AAPL calls and puts only).
- Determinism considerations: none.

## Commands
```bash
make lint
make security
make test
pytest tests/test_institutional.py -q
```
