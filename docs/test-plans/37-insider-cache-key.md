# Test Plan: insider-cache-key

Path: `docs/test-plans/37-insider-cache-key.md`

## What changed
- `analysis/cache.py`: `artifact_id_insider(ticker, transactions_text)` returns
  `{ticker}_{sha256[:16]}_v2`.
- `analysis/insider_analyzer.py`: key built from the formatted transaction and cluster text;
  prompt window line no longer has dates; same-date transactions ordered by `id`.
- `tests/test_analysis_cache.py`, `tests/test_insider_analyzer.py`: new tests.
- `tests/test_analyzer_inputs.py`: packet-30 guard no longer pins the old insider id.
- `tests/test_analyzer_cache_model.py`: insider stub is a full transaction row and the cluster
  lookup is stubbed, since the input is now formatted before the cache lookup.

## Acceptance criteria coverage
- AC1: `TestArtifactIds::test_insider_artifact_id_format`, `test_insider_artifact_id_exact_value`,
  `test_insider_artifact_id_stable_for_same_text`,
  `test_insider_artifact_id_differs_for_different_text`
- AC2: `TestCacheKey::test_unchanged_inputs_hit_cache_next_day` (real `AnalysisCache` on
  SQLite)
- AC3: `TestCacheKey::test_new_transaction_calls_api`,
  `test_transaction_leaving_window_calls_api`, `test_cluster_flag_appearing_calls_api`
- AC4: `TestCacheKey::test_prompt_has_no_score_date`
- AC5: `TestCacheKey::test_same_date_order_is_fixed`
- AC6: `make lint`, `make test`, `make security`

## Edge cases
- From spec: no transactions (existing `TestNoTransactions`); model change still misses
  (`test_model_change_still_misses`).
- Additional adversarial cases:
  - Same text under another ticker gives another id (`test_insider_artifact_id_differs_by_ticker`)
    and another ticker's cached result is not reused (`test_other_ticker_result_not_reused`)
  - A new id can never equal an old date-keyed id, so stale results are not served
    (`test_insider_artifact_id_is_not_a_date_key`)
  - The id passed to `cache.get` and `cache.set` is the hash of the exact text in the prompt
    (`test_cache_set_uses_key_from_prompt_text`)

## Notes
- Six of the new tests fail against `main` (key, prompt and ordering tests), reproducing the bug.
- Read-only check on production data for the 140 tickers insider analysed on 2026-10-20: keys
  unchanged for 135/140 (10-15 to 10-16), 136/140 (10-16 to 10-19), 138/140 (10-19 to 10-20).

## Commands
```bash
make lint
make test
make security
```
