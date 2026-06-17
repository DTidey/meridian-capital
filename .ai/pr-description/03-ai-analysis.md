## Summary
- Integrates the OpenAI API to provide qualitative enrichment of Layer 2 LONG/SHORT candidates via four analyzers: earnings call sentiment, filing forensics, 10-K risk factor extraction, and insider signal interpretation
- Blends the Layer 2 quantitative composite (60%) with an AI composite score (40%) to produce a final conviction score stored in `combined_scores`, re-ranked within GICS sector
- Implements a PostgreSQL-backed analysis cache with TTL eviction, per-run cost tracking with a hard ceiling guard, and exponential-backoff retry on rate limit and 5xx errors
- Writes one markdown report per LONG/SHORT candidate and prints a cost summary on completion

## Spec
- Spec: `docs/specs/03-ai-analysis.md`
- Test plan: `docs/test-plans/03-ai-analysis.md`
- PR draft path: `.ai/pr-description/03-ai-analysis.md`

## Acceptance Criteria
- [x] AC1: The system calls the OpenAI API using `OPENAI_API_KEY` from the environment; if the key is absent, `run_analysis.py` exits with a clear error message before making any API calls.
- [x] AC2: All API calls use `response_format={"type": "json_object"}` (JSON mode), eliminating the need for fence stripping or regex extraction; the client retries on `openai.RateLimitError` and HTTP 5xx with exponential backoff (delays 2, 4, 8, 16, 32 s; max 5 attempts).
- [x] AC3: The cost tracker checks `cost_tracker.would_exceed_ceiling()` before each API call and raises `CostCeilingExceeded` (skipping remaining analyses with a logged warning) if the cumulative spend would exceed `cost_ceiling_usd` (default $25.00).
- [x] AC4: The analysis cache reads and writes the `analysis_results` table keyed by `(analyzer, ticker, artifact_id)`; a cached result within its TTL is returned without an API call, and `evict_expired()` removes stale rows at startup.
- [x] AC5: The earnings analyzer returns None when no transcript exists for the ticker; otherwise it scores 6 categories (1-10) and stores the mean as `earnings_score` in `ai_scores`.
- [x] AC6: The risk analyzer strips HTML tags from 10-K `content_text`, truncates to `filing_risk_max_chars`, returns None when no 10-K is cached, and maps `risk_severity` (LOW/MEDIUM/HIGH/CRITICAL) to `risk_score` values of 10, 8, 6, 4.
- [x] AC7: The insider analyzer returns None when no open-market transactions (`is_open_market = 1`) exist in the 90-day window; otherwise it maps `signal_strength` (STRONG_BUY/MODERATE_BUY/NEUTRAL/MODERATE_SELL/STRONG_SELL) to `insider_ai_score` values of 10, 7.5, 5, 2.5, 1.
- [x] AC8: The combined score blends Layer 2 `quant_composite` (0-100) and AI `ai_composite` (normalised from 1-10 to 0-100) at 60%/40%; tickers with `analyzers_used == 0` use 100% quant weighting; the combined score is re-ranked within GICS sector and LONG/SHORT labels are re-applied using the same thresholds as Layer 2.
- [x] AC9: The report generator writes one markdown file per LONG/SHORT candidate to `output/reports_{YYYYMMDD}/`, covering quantitative scores, all four analyzer outputs (gracefully omitting any that returned None), upcoming catalysts from `earnings_calendar`, and sector context.
- [x] AC10: The entry point `run_analysis.py` supports `--estimate-cost` (token count via tiktoken, exit without API calls), `--ticker`, `--sector`, `--date`, and `--no-cache` flags, and prints a cost summary on completion.

## Security Review
- [x] Security considerations were reviewed and updated in the linked spec
- [x] No meaningful security impact beyond API key handling via environment variables

## Validation
- [x] `make lint`
- [x] `make test`
- [x] `make security`

## GitHub Checks
- Required checks for `main`:
  - `CI / test`
  - `CodeQL / analyze`

## Changelog
- [x] Add to `CHANGELOG.md` under `## Unreleased`

## Open Risks
- None. All tests passing. Code is read-only with respect to external systems during tests.
