# Test Plan: ai-analysis

Path: `docs/test-plans/03-ai-analysis.md`

## What changed
- Initial implementation of Layer 3: AI Qualitative Analysis Engine. All code shipped in the founding commit.

## Acceptance criteria coverage
- AC1: `tests/test_api_client.py` — `TestChatSuccess`, `TestRetry`, `TestCostGuard`, `TestEstimateTokens`
- AC2: `tests/test_api_client.py` — `TestRetry`, `TestChatSuccess`
- AC3: `tests/test_cost_tracker.py` — `TestCeiling`, `TestRecord`, `TestTotals`, `TestSummary`, `TestEstimateCost`; `tests/test_api_client.py` — `TestCostGuard`
- AC4: `tests/test_analysis_cache.py` — `TestGetMiss`, `TestGetHit`, `TestSet`, `TestEviction`, `TestArtifactIds`; `tests/test_analysis_db.py`
- AC5: `tests/test_earnings_analyzer.py` — `TestNoTransactions` (adapted for missing transcript), `TestCacheHit`, `TestApiCall`
- AC6: `tests/test_risk_analyzer.py`
- AC7: `tests/test_insider_analyzer.py` — `TestNoTransactions`, `TestInsiderScore`, `TestFormatTransactions`, `TestValidate`
- AC8: `tests/test_combined_score.py` — `TestComputeAiComposite`, `TestComputeCombinedScores`
- AC9: `tests/test_report_generator.py` — `TestGenerateReports`, `TestBuildReport`, `TestEarningsSection`, `TestRiskSection`
- AC10: `tests/test_api_client.py` — `TestEstimateTokens`; `tests/test_cost_tracker.py` — `TestSummary`

## Edge cases
- From spec:
  - `OPENAI_API_KEY` absent: `run_analysis.py` must exit with a clear error message before making any API call
  - Transcript absent for a ticker: `earnings_analyzer` returns `None`; the combined score must omit it gracefully (`analyzers_used` decremented)
  - No 10-K cached for a ticker: `risk_analyzer` returns `None`; no crash
  - No open-market transactions in 90-day window: `insider_analyzer` returns `None`
  - `analyzers_used == 0` for a ticker: combined score falls back to 100% quant weight (no penalty)
  - `CostCeilingExceeded`: remaining candidates are skipped with a logged warning listing skipped tickers; partial results are still written for completed candidates
  - Cache hit within TTL: API is not called; cached JSON is returned directly
  - Cache row expired past TTL: treated as a cache miss; fresh API call is made

- Additional adversarial cases:
  - OpenAI returns a valid HTTP 200 response with non-JSON content despite `response_format={"type": "json_object"}`: `json.loads` will raise; the client should surface the exception without retrying (JSON mode failure is not a transient error)
  - Exponential backoff exhausted after 5 retries on `RateLimitError`: the analyzer must log a warning and return `None` for that ticker, not crash the entire run
  - `filing_risk_max_chars` truncation cuts mid-word in a 10-K: the truncated string must still be valid UTF-8 (no partial multi-byte sequences) and the API call must proceed
  - `artifact_id` collision between two different tickers that share the same `accession_no` (unlikely but possible with malformed data): the unique constraint `(analyzer, ticker, artifact_id)` includes `ticker`, so each is stored independently
  - Sector analysis called with zero candidates in a sector: must be skipped without calling the API
  - Re-running analysis the same day with `--no-cache`: all `analysis_results` rows for today must be overwritten, not duplicated (upsert by `(analyzer, ticker, artifact_id)`)

## Notes
- Flaky risks: External API calls are mocked in tests; no network dependency.
- Determinism considerations: All tests use deterministic fixtures.
