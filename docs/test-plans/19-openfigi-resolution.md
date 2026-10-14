# Test Plan: openfigi-resolution

Path: `docs/test-plans/19-openfigi-resolution.md`

## What changed
- `data/institutional.py`: `ID_CINS` for non-US identifiers; US common stock only, no first-hit
  fallback; per-run identifier cache; failed batches logged at WARNING; holdings outside
  `sp500_universe` dropped; `refetch` option.
- `run_data.py`: `--refetch-13f` flag.

## Acceptance criteria coverage
- AC1: `TestOpenFigi::test_cins_queried_as_id_cins`, `test_lower_case_cins`
- AC2: `TestOpenFigi::test_no_first_hit_fallback`, `test_non_common_stock_rejected`,
  `test_cins_queried_as_id_cins` (German `AEX` hits ahead of `CB`)
- AC3: `TestUniverseFilter::test_only_universe_tickers_returned`
- AC4: `TestOpenFigi::test_cache_prevents_repeat_lookups`,
  `TestRefetch::test_shared_cache_passed_to_every_filing`
- AC5: `TestOpenFigi::test_failed_batch_warned_and_not_cached`
- AC6: `TestRefetch::test_refetch_replaces_fetched_periods_only`, `test_failed_refetch_keeps_stored_rows`,
  `test_default_keeps_rows`, `test_cli_flag`, `test_shared_cache_passed_to_every_filing`
- AC3 share classes, AC7: `TestShareClassTickers` (BRK/B, BF.B, FOX/FOXA)
- AC8: `TestOpenFigi::test_malformed_response_is_failed_batch`,
  `test_short_response_leaves_rest_unresolved`

## Edge cases
- From spec:
  - No API key: `test_no_key_skips_lookup`
  - "No identifier found." answers cached as unresolved: `test_cache_prevents_repeat_lookups`
  - Lower-case CINS: `test_lower_case_cins`
  - Fewer results than identifiers: `test_short_response_leaves_rest_unresolved`
- Additional adversarial cases:
  - ETP on a US exchange rejected: `test_non_common_stock_rejected`
  - Foreign listings listed first: `test_cins_queried_as_id_cins`
  - Failed batch retried later in the run: `test_failed_batch_warned_and_not_cached`
  - Name-resolved non-universe holding never reaches OpenFIGI or storage:
    `test_only_universe_tickers_returned`

## Notes
- Live dry run (2026-10-14, throwaway SQLite, real SEC and OpenFIGI):
  - Before (name matching only): 4,567 rows, 342 tickers, 9 tickers flagged "3+ new".
  - Key with old code: 46,129 rows (6,622 in the universe), 26 flags, 7 min, Chubb Q1 2026
    still missing.
  - Key with this packet: 6,590 rows, 485 tickers, 4 flags, 1 min 47 s, Chubb in all 6
    quarters. After the review fixes: 6,616 rows, 488 tickers, 4 flags; BRK-B, BF-B, FOX/FOXA
    and NWS/NWSA held separately.
  - Remaining one-quarter gaps (about 3.4%) are all at Bridgewater, Point72 and Citadel.
    Sampled six Bridgewater Q1 2026 gaps (ADP, ADSK, AMCR, AOS, APP, BLK): none is in its
    993-issuer filing, so they are real exits, not resolution misses.

## Commands
```bash
make lint
make security
make test
python run_data.py --no-filings --refetch-13f   # after merge, to rebuild stored 13F data
```
