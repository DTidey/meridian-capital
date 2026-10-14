# Test Plan: cusip-first-resolution

Path: `docs/test-plans/22-cusip-first-resolution.md`

## What changed
- `data/institutional.py`: `_resolve_tickers` looks up CUSIPs via OpenFIGI first and falls back to
  issuer-name matching; `_share_class_spelling` split out.

## Acceptance criteria coverage
- AC1: `TestCusipFirst::test_share_classes_resolved_by_cusip` (one call with all CUSIPs);
  updated `TestUniverseFilter::test_only_universe_tickers_returned`
- AC2: `TestCusipFirst::test_unresolved_or_non_universe_falls_back_to_name`,
  `test_failed_batch_falls_back_to_name`
- AC3: `TestCusipFirst::test_without_key_name_only`
- AC4: `TestCusipFirst::test_share_classes_resolved_by_cusip`

## Edge cases
- From spec: OpenFIGI batch failure (`test_failed_batch_falls_back_to_name`); issuer-ticker rows
  not looked up (`test_issuer_ticker_rows_not_looked_up`).
- Additional adversarial cases:
  - Empty and missing CUSIP: `test_empty_or_missing_cusip_uses_name`
  - OpenFIGI `BRK/B` spelling: `test_class_spelling_from_openfigi`
  - Old or non-universe OpenFIGI ticker: `test_unresolved_or_non_universe_falls_back_to_name`

## Notes
- Live dry run (2026-10-14, throwaway SQLite, real SEC and OpenFIGI): 6,637 holdings vs 6,616 in
  production. The only differences: 33 GOOG fund-quarters added and 12 moved from GOOGL (funds
  holding class C only). Every other holding is identical.

## Commands
```bash
make lint
make test
python run_data.py --no-filings --refetch-13f   # once, after merge
```
