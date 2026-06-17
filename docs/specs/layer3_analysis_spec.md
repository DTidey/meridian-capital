# Meridian Capital Partners — Layer 3: AI Qualitative Analysis Engine
## Implementation Specification

**Date:** 2026-05-05  
**Status:** Complete  
**Depends on:** Layer 1 (`data/`) and Layer 2 (`factors/`) — PostgreSQL populated  
**AI provider:** OpenAI (replaces Anthropic in the original prompt spec)

---

## 1. Overview

Layer 3 reads the LONG and SHORT candidates produced by Layer 2 and enriches each with qualitative AI analysis: earnings call sentiment, filing forensics, risk factor extraction, and insider signal interpretation. Results are blended with the Layer 2 quantitative composite (60% quant / 40% AI) to produce a final conviction score that feeds Layer 4.

The original prompt spec referenced the Anthropic SDK. This spec substitutes the **OpenAI Python SDK** throughout. The business logic of every analyzer is unchanged; only the API client, token counting, cost tracking, and output parsing differ.

---

## 2. Key Differences vs. Anthropic Spec

| Concern | Original (Anthropic) | This implementation (OpenAI) |
|---------|---------------------|------------------------------|
| SDK | `anthropic` | `openai` |
| Secret | `ANTHROPIC_API_KEY` | `OPENAI_API_KEY` |
| Default model | `claude-sonnet-4-5` | `gpt-4o` |
| Cheap model | — | `gpt-4o-mini` (per-analyzer override) |
| JSON output | Parse from text / fences | `response_format={"type": "json_object"}` (native JSON mode) |
| Prompt caching | Explicit `cache_control: ephemeral` | Automatic (OpenAI caches prompts > 1024 tokens at 50% discount — no configuration needed) |
| Token counting | `anthropic.count_tokens()` | `tiktoken` library |
| Usage object | `response.usage.{input,output,cache_write,cache_read}_tokens` | `response.usage.{prompt_tokens, completion_tokens}` |
| Rate limit error | `anthropic.RateLimitError` | `openai.RateLimitError` |
| Response text | `response.content[0].text` | `response.choices[0].message.content` |

---

## 3. Module Structure

```
ls_equity_fund/
├── run_analysis.py             # Layer 3 entry point
└── analysis/
    ├── __init__.py
    ├── db.py                   # New table definitions (registered on shared metadata)
    ├── api_client.py           # OpenAI SDK wrapper: retry, JSON mode, cost guard
    ├── cost_tracker.py         # Token and cost accounting
    ├── cache.py                # PostgreSQL result cache with TTL
    ├── earnings_analyzer.py    # Earnings call transcript analysis
    ├── filing_analyzer.py      # Forensic accounting review of fundamentals
    ├── risk_analyzer.py        # 10-K risk factor extraction
    ├── insider_analyzer.py     # Form 4 signal interpretation
    ├── sector_analysis.py      # Per-sector AI ranking and outlook
    ├── combined_score.py       # 60% quant + 40% AI blending
    └── report_generator.py     # Markdown report writer
```

---

## 4. Database Schema

### 4.1 New tables

`analysis/db.py` imports the global `metadata` from `data.db` and registers new tables on it — identical pattern to `factors/db.py`.

#### `analysis_results`
The AI result cache. Re-running the same analysis within the TTL returns the cached result at zero API cost.

| Column | Type | Description |
|--------|------|-------------|
| `id` | Integer PK (autoincrement) | — |
| `analyzer` | String | e.g. `earnings`, `filing`, `risk`, `insider` |
| `ticker` | String | — |
| `artifact_id` | String | Hash or ID of the source artifact (transcript date, filing accession, etc.) that uniquely identifies the input. Changing input → different `artifact_id` → new API call |
| `model` | String | Model used (e.g. `gpt-4o`) |
| `result_json` | Text | Full JSON response from the analyzer |
| `prompt_tokens` | Integer | Input tokens consumed |
| `completion_tokens` | Integer | Output tokens consumed |
| `cost_usd` | Float | Computed cost in USD at time of call |
| `created_at` | String | ISO timestamp |
| `expires_at` | String | ISO timestamp: `created_at + TTL` |

Unique constraint: `(analyzer, ticker, artifact_id)` — upsert on re-run.  
Index: `(ticker, analyzer)` for fast candidate lookups.

---

#### `ai_scores`
Stores the final per-ticker AI conviction scores (average across available analyzers), written after all analyzers complete.

| Column | Type | Description |
|--------|------|-------------|
| `ticker` | String | — |
| `score_date` | String | ISO date of the analysis run |
| `earnings_score` | Float | 1–10 average of 6 earnings categories (or NULL if no transcript) |
| `filing_score` | Float | Average of earnings_quality and balance_sheet scores (1–10) |
| `risk_score` | Float | 10 − risk_severity×2 (inverted: lower risk = higher score) |
| `insider_ai_score` | Float | Mapped from signal_strength enum (1–10) |
| `ai_composite` | Float | Equal-weighted average of available scores (1–10) |
| `analyzers_used` | Integer | Count of analyzers that returned a result (0–4) |
| `computed_at` | String | Timestamp |

Primary key: `(ticker, score_date)`

---

#### `combined_scores`
Final blended conviction score.

| Column | Type | Description |
|--------|------|-------------|
| `ticker` | String | — |
| `score_date` | String | ISO date |
| `quant_composite` | Float | Layer 2 composite score (0–100) |
| `ai_composite` | Float | AI score normalised to 0–100 |
| `combined_score` | Float | 60% quant + 40% AI, re-ranked within sector (0–100) |
| `direction` | String | `LONG` / `SHORT` / `NEUTRAL` |
| `computed_at` | String | Timestamp |

Primary key: `(ticker, score_date)`

---

### 4.2 Config additions (`config.yaml`)

```yaml
analysis:
  openai_model: gpt-4o               # default model
  openai_model_cheap: gpt-4o-mini    # override per analyzer for cost saving
  cost_ceiling_usd: 25.0             # hard abort if exceeded mid-run
  cache_ttl_days: 30                 # days before a cached result is re-fetched
  score_date: null                   # null = today; override for back-dated runs
  output_dir: output/reports

  # Per-analyzer model override (omit to use openai_model)
  analyzer_models:
    earnings:  gpt-4o          # transcripts need best reasoning
    filing:    gpt-4o-mini     # structured financials — cheaper model fine
    risk:      gpt-4o          # risk factor extraction needs nuance
    insider:   gpt-4o-mini     # straightforward signal interpretation

  # Blending weights
  combined_score:
    quant_weight: 0.60
    ai_weight:    0.40

  # Transcript truncation
  transcript_max_chars: 120000
  filing_risk_max_chars: 80000
```

---

## 5. Component Specifications

### 5.1 API Client (`analysis/api_client.py`)

Single class `OpenAIClient` wrapping the `openai.OpenAI` SDK:

```python
class OpenAIClient:
    def __init__(self, api_key: str, model: str, cost_tracker: CostTracker): ...

    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        json_mode: bool = True,
    ) -> dict: ...
```

**Responsibilities:**

- Instantiate `openai.OpenAI(api_key=api_key)` once at construction.
- All calls use `response_format={"type": "json_object"}` when `json_mode=True`. This guarantees valid JSON output and eliminates the need for fence-stripping or regex extraction.
- Retry on `openai.RateLimitError` and HTTP 5xx using exponential backoff: delays of 2, 4, 8, 16, 32 seconds; max 5 attempts. Log each retry.
- After a successful response, call `cost_tracker.record(response.usage, model)`.
- Check `cost_tracker.would_exceed_ceiling()` **before** each call; raise `CostCeilingExceeded` if true.
- Extract and return `json.loads(response.choices[0].message.content)`.

**No manual JSON fence stripping is required** — OpenAI's JSON mode guarantees a parseable JSON string in the content field when `response_format={"type": "json_object"}` is set.

---

### 5.2 Cost Tracker (`analysis/cost_tracker.py`)

Tracks cumulative token spend for the current run.

```python
class CostTracker:
    def record(self, usage: CompletionUsage, model: str) -> None: ...
    def total_cost_usd(self) -> float: ...
    def would_exceed_ceiling(self, estimated_tokens: int, model: str) -> bool: ...
    def summary(self) -> dict: ...
```

**OpenAI pricing constants** (update if rates change):

| Model | Input (per 1M tokens) | Output (per 1M tokens) | Cached input discount |
|-------|----------------------|------------------------|----------------------|
| `gpt-4o` | $2.50 | $10.00 | 50% off (automatic for prompts > 1024 tokens) |
| `gpt-4o-mini` | $0.15 | $0.60 | 50% off (automatic) |

`usage.prompt_tokens_details.cached_tokens` (if present in the response) indicates how many input tokens were served from OpenAI's automatic cache. Cost is computed accordingly.

`summary()` returns: `{model, calls, prompt_tokens, completion_tokens, cached_tokens, total_cost_usd}` — printed at the end of the run.

**Token estimation** for `--estimate-cost` mode: use `tiktoken.encoding_for_model(model)` to count tokens in prepared prompts before sending. This is an estimate (system + user prompt lengths), not an exact figure.

---

### 5.3 Analysis Cache (`analysis/cache.py`)

PostgreSQL-backed result cache. Reads and writes the `analysis_results` table.

```python
class AnalysisCache:
    def get(self, analyzer: str, ticker: str, artifact_id: str) -> dict | None: ...
    def set(self, analyzer: str, ticker: str, artifact_id: str,
            model: str, result: dict, usage: CompletionUsage, cost: float) -> None: ...
    def evict_expired(self) -> int: ...
```

- `get()` returns the parsed JSON dict if a non-expired row exists, else `None`.
- `set()` upserts using `insert_or_replace` from `data.db`.
- `artifact_id` construction per analyzer:
  - Earnings: `f"{ticker}_{earnings_date}"` (transcript date from `earnings_transcripts.earnings_date`)
  - Filing: `f"{ticker}_{most_recent_period_end}"` (latest quarterly fundamentals period)
  - Risk: `f"{ticker}_{accession_no}"` (10-K filing accession number)
  - Insider: `f"{ticker}_{window_end}"` (90-day window end date = score date)
- TTL check: `expires_at > utcnow()`. Expired rows are treated as cache misses.
- `evict_expired()` deletes all rows where `expires_at < utcnow()`; called once at startup.

---

### 5.4 Earnings Call Analyzer (`analysis/earnings_analyzer.py`)

**Input:** Full transcript text from `earnings_transcripts` table, truncated to `transcript_max_chars`.  
**Returns:** `None` if no transcript for this ticker.

**System prompt** (fixed, long — benefits from OpenAI's automatic prompt caching):
> You are a senior equity analyst specialising in earnings call analysis. You will receive an earnings call transcript and return a structured JSON assessment. Score each category 1–10 where 10 is most positive. Your response must be valid JSON with no additional text.

**User prompt:** The truncated transcript text.

**Required output JSON schema:**

```json
{
  "management_confidence": {"score": 7, "reasoning": "..."},
  "revenue_guidance":      {"score": 6, "reasoning": "..."},
  "margin_trajectory":     {"score": 8, "reasoning": "..."},
  "competitive_position":  {"score": 7, "reasoning": "..."},
  "risk_factors":          {"score": 5, "reasoning": "..."},
  "capital_allocation":    {"score": 9, "reasoning": "..."},
  "bull_case":    "...",
  "bear_case":    "...",
  "key_quotes":   ["quote1", "quote2", "quote3"],
  "one_line_summary": "..."
}
```

`earnings_score` = mean of the 6 category scores.

---

### 5.5 Filing Analyzer (`analysis/filing_analyzer.py`)

**Input:** Up to 8 quarters of fundamental metrics (from `fundamentals` table) formatted as a compact JSON table in the user prompt. No filing text needed — this is purely quantitative forensics with qualitative AI interpretation.

**System prompt:**
> You are a forensic accounting analyst. You will receive quarterly financial data for a company and return a structured JSON assessment of accounting quality. Focus on earnings quality, accruals, revenue patterns, and balance sheet health. Score 1–10 where 10 is highest quality. Your response must be valid JSON.

**Required output JSON schema:**

```json
{
  "earnings_quality_score": 7,
  "balance_sheet_score":    6,
  "green_flags": ["CFO consistently exceeds NI", "..."],
  "red_flags":   ["AR growing faster than revenue", "..."],
  "risk_level":  "MEDIUM",
  "reasoning":   "...",
  "one_line_summary": "..."
}
```

`risk_level`: `LOW` / `MEDIUM` / `HIGH` / `CRITICAL`  
`filing_score` = mean of `earnings_quality_score` and `balance_sheet_score`.

**artifact_id:** `f"{ticker}_{latest_period_end}"` — changes only when new quarterly data arrives.

---

### 5.6 Risk Analyzer (`analysis/risk_analyzer.py`)

**Input:** `content_text` from the most recent `sec_filings` row where `form_type = '10-K'`, stripped of HTML tags, capped at `filing_risk_max_chars`.  
**Returns:** `None` if no 10-K is cached for this ticker.

**System prompt:**
> You are a risk analyst specialising in SEC 10-K filings. You will receive the Risk Factors section of a 10-K. Identify material risks (those that could genuinely impact the investment thesis) and flag boilerplate language. Your response must be valid JSON.

**Required output JSON schema:**

```json
{
  "material_risks": [
    {"risk": "...", "severity": "HIGH", "category": "regulatory"},
    ...
  ],
  "new_risks":             ["risk description if new vs prior period"],
  "boilerplate_percentage": 35,
  "risk_severity":          "MEDIUM",
  "one_line_summary":       "..."
}
```

`risk_severity`: `LOW` / `MEDIUM` / `HIGH` / `CRITICAL`  
`risk_score` for blending = `10 − (severity_map[risk_severity] * 2)` where `severity_map = {LOW:0, MEDIUM:1, HIGH:2, CRITICAL:3}` → scores of 10, 8, 6, 4.

**Detecting new risks:** Pass the prior 10-K accession number (from `sec_filings` ordered by `filed_date`) as context in the user prompt if available. Ask the model to flag risks that appear new compared to a prior filing.

**artifact_id:** The 10-K `accession_no` — a new filing always triggers a fresh API call.

---

### 5.7 Insider Analyzer (`analysis/insider_analyzer.py`)

**Input:** All open-market Form 4 transactions from the past 90 days for this ticker (from `insider_transactions` where `is_open_market = 1`), plus the `insider_cluster_flags` entry if present.  
**Returns:** `None` if no transactions in the 90-day window.

**System prompt:**
> You are an equity analyst interpreting insider trading signals from SEC Form 4 filings. Distinguish between routine selling (diversification, options exercise proceeds) and meaningful open-market buying. CEO/CFO activity is the most informative. Your response must be valid JSON.

**User prompt:** A structured summary of transactions: insider name, title, transaction type, shares, price, date.

**Required output JSON schema:**

```json
{
  "signal_strength":    "MODERATE_BUY",
  "confidence":         "MEDIUM",
  "key_transactions":   ["CEO purchased 10,000 shares at $145 on 2024-04-15"],
  "reasoning":          "...",
  "one_line_summary":   "..."
}
```

`signal_strength` enum (ordered): `STRONG_BUY`, `MODERATE_BUY`, `NEUTRAL`, `MODERATE_SELL`, `STRONG_SELL`  
`insider_ai_score` mapping: STRONG_BUY→10, MODERATE_BUY→7.5, NEUTRAL→5, MODERATE_SELL→2.5, STRONG_SELL→1

**artifact_id:** `f"{ticker}_{score_date}"` — refreshes daily with new transaction data.

---

### 5.8 Sector Analysis (`analysis/sector_analysis.py`)

Runs **after** all per-ticker analyzers have completed. Groups LONG/SHORT candidates by GICS sector and asks the model to rank them within each sector.

**Input per sector:** All available analyzer outputs for candidates in that sector, formatted as a compact JSON summary.

**System prompt:**
> You are a sector analyst. Given quantitative scores and qualitative AI assessments for a set of companies in the same sector, rank them from strongest LONG to strongest SHORT and provide a sector outlook. Your response must be valid JSON.

**Required output JSON schema:**

```json
{
  "sector": "Information Technology",
  "rankings": [
    {"ticker": "NVDA", "rank": 1, "rationale": "..."},
    ...
  ],
  "top_long_idea":   {"ticker": "NVDA", "thesis": "..."},
  "top_short_idea":  {"ticker": "INTC", "thesis": "..."},
  "sector_outlook":  "POSITIVE",
  "sector_reasoning": "..."
}
```

`sector_outlook`: `VERY_POSITIVE` / `POSITIVE` / `NEUTRAL` / `NEGATIVE` / `VERY_NEGATIVE`

Results are written to a separate `sector_analysis.json` in the output directory, not stored in the database. They are included in per-ticker markdown reports.

---

### 5.9 Combined Score (`analysis/combined_score.py`)

Blends Layer 2 and Layer 3 into a final conviction score.

**Algorithm:**

1. Load `factor_scores` for `score_date` from PostgreSQL (Layer 2 output).
2. Load `ai_scores` for `score_date`.
3. Normalise `ai_composite` (1–10 scale) to 0–100: `ai_score_normalised = (ai_composite − 1) / 9 * 100`.
4. Blend: `combined_raw = quant_weight * quant_composite + ai_weight * ai_score_normalised`.
5. If `analyzers_used == 0` for a ticker, use `quant_weight = 1.0, ai_weight = 0.0` (no penalty, pure quant).
6. Re-rank `combined_raw` within GICS sector (same `sector_rank` utility from `factors/_utils.py`) to produce `combined_score` 0–100.
7. Apply LONG/SHORT labels using same thresholds as Layer 2 (`long_quintile_threshold`, `short_quintile_threshold` from config).
8. Write to `combined_scores` table (upsert).

---

### 5.10 Report Generator (`analysis/report_generator.py`)

Writes one markdown file per LONG/SHORT candidate to `output/reports_{YYYYMMDD}/`.

**Report structure per ticker:**

```markdown
# {TICKER} — {COMPANY_NAME}
**Direction:** LONG | Score: {combined_score}/100 | Sector: {sector}
**Score date:** {score_date}

---

## Quantitative Scores
| Factor | Score |
|--------|-------|
| Composite | {composite_score} |
| Momentum | {momentum_score} |
| Quality | {quality_score} |
...

## AI Analysis

### Earnings Call ({earnings_date})
**Overall score:** {earnings_score}/10
| Category | Score | Summary |
|----------|-------|---------|
...
**Bull case:** ...
**Bear case:** ...

### Filing Quality
**Score:** {filing_score}/10
**Green flags:** ...
**Red flags:** ...

### Risk Factors (10-K)
**Severity:** {risk_severity}
**Material risks:** ...

### Insider Activity (90 days)
**Signal:** {signal_strength}
...

## Upcoming Catalysts
- Earnings: {next_earnings_date} (EPS est. ${eps_estimate})

## Sector Context ({sector})
{sector_outlook} — {sector_reasoning}
```

`upcoming_catalysts` comes from the `earnings_calendar` table.

---

## 6. Entry Point (`run_analysis.py`)

```
python run_analysis.py [--estimate-cost] [--ticker AAPL] [--sector Technology] [--date YYYY-MM-DD] [--no-cache]
```

| Flag | Behaviour |
|------|-----------|
| *(no flags)* | Full run — all LONG/SHORT candidates from today's `factor_scores` |
| `--estimate-cost` | Count tokens for all planned calls via tiktoken; print cost estimate; exit without calling API |
| `--ticker AAPL` | Single ticker mode |
| `--sector Technology` | All LONG/SHORT candidates in this sector only |
| `--date YYYY-MM-DD` | Use a specific score date's candidates |
| `--no-cache` | Ignore cache hits; force fresh API calls for all analyzers |

**Execution sequence:**

```
1. Load config + resolve DB URL + evict expired cache entries
2. Load LONG/SHORT candidates from factor_scores for score_date
3. For each candidate:
   a. Run earnings_analyzer  (cache-aware)
   b. Run filing_analyzer    (cache-aware)
   c. Run risk_analyzer      (cache-aware)
   d. Run insider_analyzer   (cache-aware)
   e. Write ai_scores row
4. Run sector_analysis for each sector with candidates
5. Run combined_score (load quant + ai → blend → write combined_scores)
6. Generate markdown reports for each LONG/SHORT candidate
7. Print cost summary
```

**Cost guard:** Before each API call, `cost_tracker.would_exceed_ceiling()` is checked. If the ceiling is reached, remaining analyses are skipped and a warning is logged listing the skipped tickers.

**Estimated cost** for a full run (40 candidates, all 4 analyzers):
- `gpt-4o` for earnings + risk: ~$3–6 depending on transcript length
- `gpt-4o-mini` for filing + insider: ~$0.10–0.30
- Sector analysis (11 sectors, ~10 candidates/sector): ~$0.50–1.00
- **Total estimate: $4–8 per full run**

---

## 7. Testing Plan

Tests live in `tests/` alongside Layers 1–2. All tests use the existing `tmp_db` SQLite fixture.

### Test files

| File | Target count | Subject |
|------|-------------|---------|
| `test_analysis_db.py` | ~8 | Table creation, upsert idempotency, TTL eviction logic |
| `test_api_client.py` | ~10 | Retry logic (mocked), cost guard, JSON extraction, ceiling check |
| `test_cost_tracker.py` | ~8 | Token accumulation, pricing arithmetic, ceiling detection, summary |
| `test_analysis_cache.py` | ~10 | Cache hit/miss, TTL expiry, artifact_id construction, eviction |
| `test_earnings_analyzer.py` | ~8 | Prompt construction, score averaging, None on missing transcript |
| `test_filing_analyzer.py` | ~8 | Financials formatting, score derivation, artifact_id from period_end |
| `test_risk_analyzer.py` | ~8 | HTML stripping, char truncation, severity→score mapping, None on no 10-K |
| `test_insider_analyzer.py` | ~8 | Signal→score mapping, transaction summary formatting, None on no data |
| `test_combined_score.py` | ~10 | 60/40 blending, pure-quant fallback, sector re-ranking, label assignment |
| `test_report_generator.py` | ~6 | File created, required sections present, None analyzer handled gracefully |

**Target total: ~84 tests**

All OpenAI API calls are mocked using `pytest-mock` and `responses` (already in `requirements.txt`). No real API calls are made in the test suite.

---

## 8. New Dependencies

Add to `requirements.txt`:

```
openai>=1.30.0
tiktoken>=0.7.0
```

The `openai` package bundles the async client and structured output support. `tiktoken` is used only for the `--estimate-cost` pre-flight token count.

---

## 9. Secrets

Add to `.env.example`:

```
OPENAI_API_KEY=sk-...
```

`OPENAI_API_KEY` is loaded from `.env` via `python-dotenv` at process start (same pattern as all other API keys). If absent, `run_analysis.py` exits with a clear error message on startup — no partial runs.

---

## 10. Implementation Order

1. Add `openai` and `tiktoken` to `requirements.txt`
2. Add `analysis:` block to `config.yaml`
3. `analysis/db.py` — new tables registered on shared metadata
4. `analysis/cost_tracker.py` + `test_cost_tracker.py`
5. `analysis/api_client.py` + `test_api_client.py`
6. `analysis/cache.py` + `test_analysis_cache.py`
7. `analysis/earnings_analyzer.py` + `test_earnings_analyzer.py`
8. `analysis/filing_analyzer.py` + `test_filing_analyzer.py`
9. `analysis/risk_analyzer.py` + `test_risk_analyzer.py`
10. `analysis/insider_analyzer.py` + `test_insider_analyzer.py`
11. `analysis/sector_analysis.py` (no dedicated test — depends on all analyzers)
12. `analysis/combined_score.py` + `test_combined_score.py`
13. `analysis/report_generator.py` + `test_report_generator.py`
14. `analysis/db.py` schema tests — `test_analysis_db.py`
15. `run_analysis.py` entry point
16. Update `docs/architecture.md`

---

## 11. Open Questions / Decisions

| Question | Recommendation |
|----------|---------------|
| Should `gpt-4o-mini` be used for filing and insider analyzers by default? | Yes — structured financial data is easier to interpret than prose; the cheaper model is sufficient |
| Should sector analysis run even when only 1 candidate exists in a sector? | Yes, but skip if 0 candidates. Single-candidate sectors produce a brief sector outlook only (no rankings) |
| Should `combined_scores` replace or supplement the Layer 2 `factor_scores` LONG/SHORT labels? | Supplement — Layer 2 scores remain unchanged; Layer 3 adds `combined_scores` as the primary signal for Layer 4 |
| How to handle the case where a ticker has a cached stale result and `--no-cache` is not set? | The TTL check in `cache.get()` treats it as a miss and triggers a fresh call. No manual invalidation needed |
| What if `gpt-4o` is rate-limited and the retry budget is exhausted? | Log a warning, skip the analyzer for that ticker, record `None`, continue with the remaining candidates — do not abort the run |
| Should `run_analysis.py` auto-trigger `run_scoring.py` first? | No — independent entry points. The user controls the pipeline order |
