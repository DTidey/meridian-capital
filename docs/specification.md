# Meridian Capital Partners — System Specification

## 1. Purpose

Meridian Capital Partners is a simulated long/short equity fund system. It ingests market, fundamental, and alternative data for the S&P 500 universe, scores securities across multiple factor dimensions, applies Claude AI analysis, constructs a market-neutral portfolio, manages risk, and routes orders through a live brokerage. A Streamlit dashboard provides daily reporting.

The system is organised into seven sequential layers, each building on the output of the previous one. All seven layers are implemented and operational.

---

## 2. Layer 1: Data Ingestion

### 2.1 Universe

- S&P 500 constituents scraped from Wikipedia, refreshed at most once every 7 days (configurable). Ticker symbols containing `.` are normalised to `-` to match exchange conventions.
- 18 benchmark and macro tickers stored alongside the universe: broad-market ETFs (SPY, QQQ, IWM, DIA), 11 GICS sector ETFs, and macro instruments (^VIX, TLT, HYG).
- Total tracked universe: ~521 instruments.

### 2.2 Market Prices

- Daily OHLCV bars fetched for all universe and benchmark tickers.
- Three-year lookback on initial run; subsequent runs are incremental (only dates after the last stored bar are requested).
- Primary source: Polygon.io (if `POLYGON_API_KEY` is set). Fallback: yfinance, fetched in batches of 100 tickers.

### 2.3 Fundamentals

- Quarterly and annual financial statements: income statement, balance sheet, and cash flow statement.
- 24 derived ratios computed and stored per period: ROE, ROA, gross/operating/net margin, revenue and earnings growth (YoY and QoQ), debt/equity, FCF yield, current ratio, accruals ratio, asset turnover, working capital, and others.
- Primary source: FMP (if `FMP_API_KEY` is set). Fallback: yfinance.

### 2.4 Short Interest

- Daily snapshot of shares short, short ratio, and short percent of float per ticker, sourced from yfinance `.info`.
- One snapshot per ticker per calendar day; existing records for today are skipped.

### 2.5 Analyst Estimates

- Daily snapshot of forward EPS estimate, consensus price target, and analyst count per ticker, sourced from yfinance `.info`.
- One snapshot per ticker per calendar day.

### 2.6 Earnings Calendar

- Upcoming earnings dates fetched via yfinance `.calendar` for a configurable lookahead window (default: 30 days).
- EPS estimate stored where available.

### 2.7 Earnings Transcripts

- Most recent three earnings call transcripts per ticker fetched from FMP (requires `FMP_API_KEY`).
- Stored with earnings date, quarter label, year, and full transcript text.
- Silently skipped if no FMP key is configured.

### 2.8 SEC EDGAR Filings

- Forms 10-K, 10-Q, 8-K, and Form 4 fetched from SEC EDGAR for all universe tickers.
- Per-form lookback windows (configurable): 10-K 400 days, 10-Q 270 days, 8-K 90 days, Form 4 180 days.
- Rate-limited to 8 requests/second in compliance with SEC fair-use policy; `SEC_USER_AGENT` and `SEC_USER_EMAIL` must be set.
- Full text of 10-K and 10-Q filings is fetched and stored.
- Form 4 (insider transactions) are parsed from XML: insider name, title, transaction code/type, shares, price, date, ownership type. CEO/CFO transactions are flagged separately.
- Cluster-buy detection: if three or more distinct insiders purchase shares within a rolling 30-day window (configurable) and within the 180-day lookback, a flag record is written to `insider_cluster_flags`.

### 2.9 Institutional Holdings (13-F)

- Quarterly 13-F filings fetched for nine tracked funds: Citadel Advisors, Point72 Asset Management, Bridgewater Associates, Tiger Global Management, Third Point, Berkshire Hathaway, Appaloosa Management, Baupost Group, and Pershing Square Capital.
- Holdings XML parsed; rows without a ticker are discarded.
- A summary table (`institutional_summary`) is rebuilt after each fund load: funds holding count, net share change vs. prior quarter, and new position count (fund holds in current quarter but not prior).

---

## 3. All System Layers

| Layer | Directory | Status | Description |
|-------|-----------|--------|-------------|
| 1 | `data/` | Complete | Market, fundamental, and alternative data ingestion for the S&P 500 universe into PostgreSQL |
| 2 | `factors/` | Complete | 27 sub-factors across 8 dimensions (momentum, value, quality, growth, revisions, short interest, insider, institutional) blended into a composite score with regime-conditional weights |
| 3 | `analysis/` | Complete | OpenAI-driven qualitative analysis of earnings transcripts, SEC filings, and insider signals; 60/40 blend with Layer 2 quantitative scores |
| 4 | `portfolio/` | Complete | Target portfolio construction (20 longs / 20 shorts, $10M NAV) via conviction-tilt or MVO optimisation; sector-neutral, beta-hedged |
| 5 | `risk/` | Complete | Pre-trade veto gate (8 checks), circuit breakers, tail-risk monitors, Barra-style factor risk model, stress testing |
| 6 | `execution/` | Complete | Alpaca limit-order routing with chunking, fill tracking, shortability cache, and live position reconciliation |
| 7 | `reporting/` + `dashboard/` | Complete | Nightly P&L attribution, tear sheets, LP letters; six-page Streamlit dashboard with JARVIS AI commentary |

---

## 4. Entry Points and CLI

Each layer has its own entry point script. A unified orchestrator (`run_all.py`) runs all layers in sequence.

| Script | Layer | Description |
|--------|-------|-------------|
| `run_data.py` | 1 | Data ingestion — universe, prices, fundamentals, SEC, 13-F |
| `run_scoring.py` | 2 | Factor scoring — compute and store all factor and composite scores |
| `run_analysis.py` | 3 | AI analysis — OpenAI enrichment of candidates, combined score |
| `run_portfolio.py` | 4 | Portfolio construction — target positions via conviction-tilt or MVO |
| `run_risk_check.py` | 5 | Risk gate — pre-trade veto checks, circuit breakers, stress tests |
| `run_execution.py` | 6 | Order execution — submit, poll, and reconcile Alpaca orders |
| `run_reporting.py` | 7 | Reporting — P&L attribution, tear sheet, LP letter |
| `run_all.py` | 1–7 | Full daily run — orchestrates all layers in sequence |

### Layer 1 (`run_data.py`)

```
python run_data.py [--no-filings] [--no-13f] [--forms FORM ...] [--force-universe] [--tickers TICKER ...]
```

| Flag | Effect |
|------|--------|
| `--no-filings` | Skip SEC EDGAR fetch (10-K, 10-Q, 8-K, Form 4) |
| `--no-13f` | Skip 13-F institutional holdings fetch |
| `--forms FORM [FORM ...]` | Fetch only the specified SEC form types |
| `--force-universe` | Refresh the S&P 500 universe from Wikipedia even if the cache is fresh |
| `--tickers TICKER [...]` | Restrict the run to specific tickers (for testing or targeted updates) |

The script logs to both stdout and `output/run.log`, and prints a summary table of counts on completion.

### Full daily run (`run_all.py`)

See `docs/run_all.md` for common invocation patterns including `--whatif`, `--dry-run`, `--stress`, and `--no-execution`.

---

## 5. Configuration

All runtime parameters live in `config.yaml`. The database connection URL is read from the `DATABASE_URL` environment variable if set, otherwise from `config.yaml`. API keys are read exclusively from environment variables (via `.env` or shell environment).

### 5.1 Key configuration parameters

| Section | Key | Default | Description |
|---------|-----|---------|-------------|
| `database` | `url` | PostgreSQL localhost | SQLAlchemy connection URL |
| `universe` | `cache_refresh_days` | 7 | How often to re-scrape Wikipedia |
| `market_data` | `lookback_years` | 3 | Historical price depth |
| `sec` | `rate_limit_per_sec` | 8 | EDGAR request rate cap |
| `sec` | `insider_lookback_days` | 180 | Form 4 fetch window |
| `sec` | `cluster_buy_window_days` | 30 | Rolling window for cluster detection |
| `sec` | `cluster_buy_min_insiders` | 3 | Minimum insiders to trigger a cluster flag |
| `earnings_calendar` | `lookahead_days` | 30 | Upcoming earnings window |

### 5.2 Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | No | Overrides `config.yaml` database URL |
| `POSTGRES_PASSWORD` | No | Used by Docker Compose; defaults to `meridian` |
| `SEC_USER_EMAIL` | Yes (SEC) | Contact email for SEC EDGAR fair-use header |
| `SEC_USER_AGENT` | Yes (SEC) | User-agent string for SEC requests |
| `POLYGON_API_KEY` | No | Enables licensed Polygon price data |
| `FMP_API_KEY` | No | Enables FMP fundamentals and transcripts |
| `FRED_API_KEY` | No | Enables FRED macro data (reserved for Layer 2+) |

---

## 6. Data Freshness and Idempotency

All ingestion operations are idempotent:

- Universe and benchmark tickers upsert on primary key (replace on conflict).
- Prices upsert on `(ticker, date)` — re-running overwrites any corrected data.
- Fundamentals upsert on `(ticker, period_type, period_end)`.
- Short interest, estimates, and earnings calendar skip records already present for today.
- SEC filings skip accession numbers already stored.
- Insider transactions and institutional holdings use insert-or-ignore on their unique constraints.
- 13-F funds already loaded for a given report date are skipped entirely.

---

## 7. Testing

The test suite covers all seven layers. Tests run against SQLite in-memory databases via pytest fixtures — no running database is required.

```
cd ls_equity_fund
python -m pytest tests/
```

Current coverage: 505 tests, all passing.
