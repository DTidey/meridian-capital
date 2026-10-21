# Insider Analysis Cache Key

**Spec file:** `docs/specs/37-insider-cache-key.md`
**Spec slug:** insider-cache-key
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-21

## Problem statement
The insider analyzer calls the AI for nearly the same tickers every night. Its cache key is
`{ticker}_{score_date}`, so the cache never hits on a new day. Run logs 2026-10-15 to 2026-10-20:
136-140 insider calls a night, 122-131 of them for the same tickers as the night before. Last
night insider made 140 of 169 API calls and took most of the 16-minute analysis stage. Filing,
risk and earnings are keyed on their source document and do not repeat.

Goal: call the AI for a ticker's insider activity only when its inputs change, meaning a Form 4
transaction or cluster flag enters or leaves the 90-day window.

## Scope
In scope:
- `AnalysisCache.artifact_id_insider`: key on a hash of the transaction text sent to the model.
- `analysis/insider_analyzer.py`: build the key from that text; the prompt no longer contains
  the score date; transactions are fetched in a fixed order.

Out of scope / non-goals:
- Sector analysis, which stays uncached and runs every night (owner's call).
- Other analyzers' cache keys, the cache TTL, and the `analysis_results` schema.
- Deleting old date-keyed insider rows; they expire through the existing 30-day TTL.

## Assumptions
- The model's only per-ticker input besides the ticker is the formatted transaction and
  cluster text (checked in `analyse`). If that text is unchanged, a new call would get the same
  input apart from the window dates.
- Every transaction line carries its own date, so the model can still judge recency without the
  window dates. The 30-day cache TTL bounds how long one result is reused.
- `insider_transactions.id` is a stable autoincrement primary key, so it breaks date ties
  deterministically.
- The dashboard reads the newest insider row by `created_at`, not by `artifact_id`
  (`dashboard/ticker_research.py`), so new keys need no reader change.

## Proposed behavior / API
### Public interface
- `AnalysisCache.artifact_id_insider(ticker: str, transactions_text: str) -> str` returns
  `f"{ticker}_{digest}_v2"`, where `digest` is the first 16 hex characters of the SHA-256 of
  `transactions_text` (UTF-8). The `_v2` suffix marks the input format, as for filing and risk.
- `insider_analyzer.analyse`: same signature and return value.

### Inputs / outputs
- Inputs: unchanged (DB rows for the ticker in the 90-day window ending at `score_date`).
- Outputs: unchanged result dict. Cache rows for insider use the new key.
- Prompt: the line `Analysis window: <start> to <score_date>` becomes
  `Analysis window: 90 days of open-market transactions`. Nothing else in the prompt changes.
- Error handling: unchanged.

### Examples
```python
text = _format_transactions(txns, cluster)
AnalysisCache.artifact_id_insider("AAPL", text)  # "AAPL_3f9a0c1d2e4b5a67_v2"
```

## Acceptance criteria
- AC1: `artifact_id_insider` returns `{ticker}_{16 hex chars}_v2`; the same text gives the same
  id, and different text gives a different id.
- AC2: For a ticker whose transactions and cluster flag are unchanged, `analyse` on a later
  `score_date` reuses the cached result and makes no API call (using a real `AnalysisCache`).
- AC3: A new transaction in the window, a transaction leaving the window, or a cluster flag
  appearing changes the key, so `analyse` calls the API.
- AC4: The prompt sent to the model does not contain `score_date` or the window start date.
- AC5: Transactions sharing a date are fetched in a fixed order (date, then `id`, newest first),
  so the key does not depend on database row order.
- AC6: `make lint`, `make test` and `make security` pass.

## Security considerations
- Auth/authz impact: none.
- Input handling or injection risk: none new; the hash input is the text already sent to the
  model, and the id is used only as a bound SQL parameter.
- Secrets or credential handling: none.
- Data exposure or privacy impact: none; the id holds a hash, not transaction details.
- File system access impact: none.
- Network or external service impact: fewer OpenRouter calls.
- Dependency or supply-chain impact: none (`hashlib` is standard library).
- Security notes for reviewers/testers: SHA-256 is used for identity, not secrecy; a 16-hex
  (64-bit) prefix per ticker makes an accidental collision negligible.

## Edge cases
- No transactions in the window: returns `None` before any key is built (unchanged).
- More than 50 transactions: the 50 newest are used, as now; a new one changes the set and key.
- Same transactions, model changed: `cache.get(model=...)` already treats it as a miss.
- Cluster flag present without new transactions: part of the hashed text, so it changes the key.

## Test guidance
- AC1 -> `tests/test_analysis_cache.py::TestArtifactIds::test_insider_artifact_id*`
- AC2 -> `tests/test_insider_analyzer.py::TestCacheKey::test_unchanged_inputs_hit_cache_next_day`
- AC3 -> `tests/test_insider_analyzer.py::TestCacheKey` (new txn, aged-out txn, cluster flag)
- AC4 -> `tests/test_insider_analyzer.py::TestCacheKey::test_prompt_has_no_score_date`
- AC5 -> `tests/test_insider_analyzer.py::TestCacheKey::test_same_date_order_is_fixed`
- AC6 -> `make lint`; `make test`; `make security`

## Decision log
- 2026-10-21: Hash the formatted transaction text rather than list transaction ids: it is
  exactly what the model sees, so any input change (including the cluster flag) changes the key.
- 2026-10-21: Drop the window dates from the prompt: they change daily and would force a new
  call; each transaction line keeps its own date.
- 2026-10-21: Sector analysis stays uncached (owner's call).
