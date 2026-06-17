# Meridian Capital Partners — Layer 3: Claude AI Qualitative Analysis

Build Layer 3 of the Meridian Capital Partners hedge fund. Layers 1–2 are built.  
Build the Claude API qualitative analysis layer — the AI analyst that reads filings, financials, and insider data.

- **SDK:** Anthropic SDK. Key: `ANTHROPIC_API_KEY` in `.env`
- **Default model:** `claude-sonnet-4-5` (configurable)
- **Prompt caching:** enabled on all system prompts (`cache_control: ephemeral`)

---

## Components

### 1. API Client (`analysis/api_client.py`)

- Anthropic SDK wrapper
- Prompt caching (`cache_control: ephemeral`) on every system prompt
- Retry on 429/5xx with exponential backoff
- JSON extraction: handle raw JSON, ` ```json ` fences, and prose-wrapped JSON
- Token count estimator for cost prediction

---

### 2. Cost Tracker (`analysis/cost_tracker.py`)

- Read `response.usage` after every call
- Track: input / output / cache-write / cache-read tokens
- Hard cost ceiling from config (default: **$25/run**) — abort if exceeded

---

### 3. Analysis Cache (`analysis/cache.py`)

- SQLite table `analysis_results` keyed by `(analyzer, ticker, artifact_id)`
- TTL-based eviction (default 30 days)
- Re-running same artifact = free cache hit

---

### 4. Earnings Call Analyzer (`analysis/earnings_analyzer.py`)

- **Input:** transcript from `data/transcripts` (requires FMP key). Truncate to 120K chars
- **Score 1–10 across 6 categories:**
  - Management Confidence
  - Revenue Guidance
  - Margin Trajectory
  - Competitive Position
  - Risk Factors
  - Capital Allocation
- **Output JSON:** per-category reasoning, `bull_case`, `bear_case`, `key_quotes`, `one_line_summary`
- Return `None` if no transcript

---

### 5. Filing Analyzer (`analysis/filing_analyzer.py`)

- **Input:** 8 quarters of fundamental metrics. Forensic accounting review
- **Assess:**
  - Earnings quality (CFO vs NI)
  - Revenue quality (AR vs revenue)
  - Balance sheet health
  - Accruals
- **Output JSON:** `earnings_quality_score`, `balance_sheet_score`, `red/green flags`, `risk_level`

---

### 6. Risk Analyzer (`analysis/risk_analyzer.py`)

- **Input:** 10-K Risk Factors section (strip HTML, cap 80K chars)
- Separate material risks from boilerplate
- Flag new risks vs prior filing
- **Output JSON:** `new_risks`, `material_risks`, `boilerplate_percentage`, `risk_severity`, `one_line_summary`
- Return `None` if no 10-K cached

---

### 7. Insider Analyzer (`analysis/insider_analyzer.py`)

- **Input:** Form 4 data (last 90 days)
- Interpret: routine selling vs meaningful buying
- **Output JSON:** `signal_strength` (STRONG_BUY to STRONG_SELL), `confidence`, `key_transactions`, `reasoning`, `one_line_summary`
- Return `None` if no insider data

---

### 8. Sector Analysis (`analysis/sector_analysis.py`)

- Per sector: gather all Claude results, rank by fundamental quality and positioning
- **Output:** rankings with reasoning, `top_long_idea`, `top_short_idea`, `sector_outlook`

---

### 9. Combined Score (`analysis/combined_score.py`)

- **60%** quantitative composite (Layer 2) + **40%** Claude fundamental (avg of available analyzers)
- If no Claude analysis available, use 100% quantitative — no penalty
- Re-rank within sector

---

### 10. Report Generator (`analysis/report_generator.py`)

- Per LONG/SHORT candidate: markdown report with all scores, Claude summaries, upcoming catalysts, risk flags
- Save to `output/reports_{timestamp}/{TICKER}.md`

---

## Entry Point

**`run_analysis.py`**

| Flag | Description |
|------|-------------|
| `--estimate-cost` | Preview cost before running |
| `--ticker AAPL` | Single stock mode |
| `--sector Technology` | Sector-only run |
| *(no flag)* | Full run |

> Estimated cost for full run (20 long + 20 short candidates): **$2–5** using Sonnet.
