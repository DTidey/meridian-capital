# Meridian Capital Partners — Technical Architecture

## 1. System Overview

The application is a Python monorepo at `ls_equity_fund/`. It is structured as seven sequential processing layers, each in its own subdirectory. All seven layers are implemented.

```
ls_equity_fund/
    ├── run_data.py             # Layer 1: data ingestion entry point
    ├── run_scoring.py          # Layer 2: factor scoring entry point
    ├── run_transcripts.py      # Earnings transcript ingestion (between Layers 2 and 3)
    ├── run_analysis.py         # Layer 3: AI analysis entry point
    ├── run_portfolio.py        # Layer 4: portfolio construction entry point
    ├── run_risk_check.py       # Layer 5: risk gate entry point
    ├── run_execution.py        # Layer 6: order execution entry point
    ├── run_reporting.py        # Layer 7: reporting entry point
    ├── run_all.py              # Full daily run orchestrator
    ├── config.yaml             # Runtime parameters
    ├── .env                    # API keys and secrets (not committed)
    ├── data/                   # Layer 1: ingestion
    ├── factors/                # Layer 2: factor scoring
    ├── analysis/               # Layer 3: LLM qualitative analysis, filing summaries
    ├── portfolio/              # Layer 4: portfolio construction
    ├── risk/                   # Layer 5: risk management
    ├── execution/              # Layer 6: Alpaca order routing
    ├── reporting/              # Layer 7: P&L reports and tear sheets
    ├── dashboard/              # Layer 7: Streamlit live dashboard
    ├── tests/                  # Test suite (pytest)
    ├── cache/                  # Runtime cache (predicted covariance, shortability, halt lock)
    └── output/                 # Log files (gitignored)
```

---

## 2. Technology Stack

| Concern | Choice |
|---------|--------|
| Language | Python 3.12 |
| Database | PostgreSQL 16 (separate server) |
| ORM / query layer | SQLAlchemy 2.0 (Core, not ORM) |
| DB adapter | psycopg2-binary 2.9 |
| Data manipulation | pandas 3.x |
| Market data (free) | yfinance |
| Market data (licensed) | Polygon.io REST API |
| Fundamentals | yfinance (an FMP backend exists but is not selected) |
| Earnings transcripts | FMP REST API (optional), else 8-K Exhibit 99 from SEC EDGAR |
| Macro data | FRED API (optional; tail-risk monitor) |
| 13-F ticker resolution | OpenFIGI API (optional, CUSIP to ticker) |
| HTML parsing | BeautifulSoup + lxml (EDGAR filing index and documents) |
| Regulatory filings | SEC EDGAR REST API (public) |
| AI analysis | OpenAI models via the OpenAI API or OpenRouter (`llm.provider`); per-analyzer models in `analysis.analyzer_models` (earnings and risk on gpt-6-sol, filing and insider on gpt-6-luna); `analysis.openai_model` for JARVIS, the LP letter and commentary; `analysis.openai_model_cheap` for filing summaries |
| Token counting | tiktoken |
| Portfolio optimisation | scipy (SLSQP via `scipy.optimize.minimize`) |
| Parquet I/O | pyarrow (predicted covariance cache) |
| Brokerage | alpaca-py (Alpaca Markets REST API) |
| Dashboard | Streamlit + Plotly |
| Linting | ruff |
| Testing | pytest, pytest-mock, responses |
| Config | PyYAML + python-dotenv |

---

## 3. Infrastructure

### 3.1 Database

PostgreSQL 16 runs on a separate server (database `meridian`, port 5432), not on the development machine.

The connection URL, credentials included, comes only from the `DATABASE_URL` environment variable, set in the git-ignored `.env`. `data.db.database_url()` reads it for every entry point and raises a `RuntimeError` if it is missing; there is no fallback. Entry points that log the URL mask the password with `data.db.redacted_url()`.

### 3.2 SQLite (tests)

The engine factory (`get_engine`) supports any SQLAlchemy URL. Passing a `sqlite:///path/to/file.db` URL is valid and is used by the test suite. The factory applies SQLite-specific PRAGMAs (`journal_mode=WAL`, `foreign_keys=ON`) automatically when a SQLite dialect is detected; these are skipped for PostgreSQL.

---

## 4. Layer 1: Data Ingestion

### 4.1 Module structure

```
data/
├── db.py               # Engine factory, schema definition, upsert helpers
├── providers.py        # API key detection and provider selection
├── universe.py         # S&P 500 + benchmark ticker management
├── market_data.py      # Daily OHLCV prices
├── fundamentals.py     # Financial statements + derived ratios (period-aligned, history kept)
├── short_interest.py   # Short interest snapshots
├── estimates.py        # Analyst EPS, price target and EPS trend snapshots
├── earnings_calendar.py# Upcoming earnings dates (.info + earningsTrend)
├── yahoo_info.py       # Yahoo retry/backoff, shared .info per run, second pass, coverage log
├── transcripts.py      # Earnings call transcripts (FMP) or 8-K earnings releases
├── edgar_docs.py       # On-demand EDGAR filing fetch and HTML-to-markdown (dashboard viewer)
├── institutional.py    # 13-F holdings: filing selection, parsing, CUSIP/name resolution, summary
└── sec_data.py         # SEC EDGAR filings, Form 4 parsing, cluster detection
```

### 4.2 Entry point (`run_data.py`)

The script runs nine ingestion steps in sequence against a single open database connection. SEC filings and transcripts (`--no-filings`) and 13-F holdings (`--no-13f`) can be skipped for faster daily runs; `--refresh-prices`, `--refresh-fundamentals` and `--refetch-13f` force a full re-fetch of those datasets. Prices also re-download a single ticker's full history on their own when a split or dividend has re-adjusted it or it has a missing date. `--rebuild-insider` runs only the universe step and then rebuilds insider transactions and cluster flags from stored Form 4 XML. A summary table is printed at the end.

SEC CIKs come from SEC's `company_tickers.json`, loaded once per run; if it fails to load, no SEC data is stored that run. Form 4 transactions are stored only when the filing's issuer CIK is the ticker's CIK.

Steps 4-6 fetch from Yahoo with retry and backoff and a second pass over failed tickers
(`data/yahoo_info.py`). Steps 4-6 share one `YahooInfo` and steps 5-6 one `YahooTrend`, so each
ticker's `.info` and `earningsTrend` are each requested at most once per run, and there is no
`.calendar` request. Each step logs its coverage and an ERROR below 95%, without stopping the run.

Execution sequence:

```
1. Universe         → sp500_universe, benchmark_tickers
2. Prices           → daily_prices
3. Fundamentals     → fundamentals
4. Short interest   → short_interest, market_caps
5. Estimates        → analyst_estimates, estimate_trends
6. Earnings cal.    → earnings_calendar
7. SEC filings      → sec_filings, insider_transactions, insider_cluster_flags
8. Transcripts      → earnings_transcripts
9. 13-F holdings    → institutional_holdings, institutional_summary
```

### 4.3 Provider abstraction

`Providers` (in `providers.py`) resolves which data source to use for each data type based solely on the presence of API keys in the environment. No code outside the ingestion modules needs to know which provider is active.

| Data type | Free fallback | Licensed upgrade |
|-----------|--------------|-----------------|
| Prices | yfinance | Polygon.io (`POLYGON_API_KEY`) |
| Fundamentals | yfinance | (yfinance only; the FMP backend is not selected) |
| Transcripts | 8-K Exhibit 99 from SEC EDGAR | FMP (`FMP_API_KEY`) |
| Macro | (none) | FRED (`FRED_API_KEY`) |
| 13-F tickers | Issuer-name match | OpenFIGI CUSIP lookup (`OPENFIGI_API_KEY`) |

### 4.4 Database layer (`db.py`)

**Schema** is defined entirely in SQLAlchemy Core `Table` objects — no ORM models. All DDL is managed by `metadata.create_all(engine, checkfirst=True)`, making schema initialisation idempotent.

**Engine factory** (`get_engine(url: str)`) accepts any SQLAlchemy URL. For SQLite URLs it creates parent directories and registers the PRAGMA hook; for other dialects it creates the engine directly.

**Upsert helpers** abstract the dialect difference between SQLite (`OR REPLACE` / `OR IGNORE`) and PostgreSQL (`ON CONFLICT DO UPDATE` / `ON CONFLICT DO NOTHING`):

- `insert_or_replace(conn, table)` — builds an upsert that overwrites all non-PK columns on a primary-key conflict. Used for time-series tables where the latest value should always win.
- Fundamentals use their own upsert (`data/fundamentals.py` `_upsert_period`) that keeps stored figures where the new fetch is blank, so history builds up.
- `insert_or_ignore(conn, table)` — builds an insert that silently discards duplicates. Used for append-only tables with natural unique keys (filings, transactions, transcripts).

Both functions introspect the table's primary key at call time and delegate to the correct dialect-specific insert class (`sqlalchemy.dialects.postgresql.insert` or `sqlalchemy.dialects.sqlite.insert`).

---

## 5. Database Schema

### 5.1 Universe and reference

| Table | Primary key | Description |
|-------|-------------|-------------|
| `sp500_universe` | `ticker` | S&P 500 constituents with GICS sector/sub-industry |
| `benchmark_tickers` | `ticker` | ETFs and macro instruments used as benchmarks |

### 5.2 Market data

| Table | Primary key | Description |
|-------|-------------|-------------|
| `daily_prices` | `(ticker, date)` | OHLCV + adjusted close, daily bars |
| `fundamentals` | `(ticker, period_type, period_end)` | Financial statement fields and derived ratios; `period_end` is a calendar month-end |

### 5.3 Signals and snapshots

| Table | Primary key / unique | Description |
|-------|---------------------|-------------|
| `short_interest` | `(ticker, date)` | Shares short, short ratio, short % of float |
| `market_caps` | `(ticker, date)` | Yahoo `marketCap` (all share classes); value, quality and the Ticker page take market cap from it |
| `analyst_estimates` | `(ticker, date)` | Forward EPS, price target, analyst count |
| `estimate_trends` | `(ticker, date, period)` | Yahoo consensus EPS for `0y` / `+1y`, now and 7/30/60/90 days ago; feeds revisions and the forward yield |
| `earnings_calendar` | `(ticker, earnings_date)` | Upcoming earnings dates + EPS estimate |

### 5.4 SEC regulatory

| Table | Primary key / unique | Description |
|-------|---------------------|-------------|
| `sec_filings` | `id` (auto); unique `accession_no` | 10-K/10-Q/8-K metadata and full text |
| `insider_transactions` | `id` (auto); unique `(ticker, accession_no, txn_index)` | Form 4 parsed transactions; `txn_index` = 0-based position among the filing's non-derivative transactions |
| `insider_cluster_flags` | `(ticker, window_start)` | Rolling-window cluster-buy detections |

### 5.5 Institutional

| Table | Primary key / unique | Description |
|-------|---------------------|-------------|
| `institutional_holdings` | `id` (auto); unique `(fund_name, ticker, report_date)` | 13-F positions per fund per quarter (`report_date` = quarter end, `filed_date` = filing date; S&P 500 only) |
| `institutional_summary` | `(ticker, report_date)` | Aggregated: fund count, net share change, new positions |

### 5.6 Transcripts

| Table | Primary key / unique | Description |
|-------|---------------------|-------------|
| `earnings_transcripts` | `id` (auto); unique `(ticker, earnings_date)` | Transcript text from FMP, or the 8-K earnings release (`source_accession_no`) |

### 5.7 Indexes

Five secondary indexes are defined alongside the schema to support common query patterns:

| Index | Table | Column |
|-------|-------|--------|
| `idx_prices_ticker` | `daily_prices` | `ticker` |
| `idx_prices_date` | `daily_prices` | `date` |
| `idx_fund_ticker` | `fundamentals` | `ticker` |
| `idx_insider_ticker` | `insider_transactions` | `ticker` |
| `idx_inst_ticker` | `institutional_holdings` | `ticker` |

---

## 6. Testing Architecture

Tests live in `tests/` and run against temporary-file SQLite databases created per-test via pytest fixtures. No external services or running database are required.

### 6.1 Fixtures (`conftest.py`)

| Fixture | Scope | Description |
|---------|-------|-------------|
| `tmp_engine` | function | SQLite engine backed by a temporary file; schema initialised |
| `tmp_db` | function | Open `Connection` from `tmp_engine`; closed on teardown |
| `config` | function | Parsed `config.yaml` |

### 6.2 Test files

**Layer 1 — Data ingestion**

| File | Subject |
|------|---------|
| `test_db.py` | Schema creation, indexes, WAL mode, PK/unique constraint behaviour |
| `test_universe.py` | Cache freshness logic, Wikipedia HTML parsing, ticker normalisation |
| `test_market_data.py` | OHLCV parsing, incremental updates, batch fetching |
| `test_fundamentals.py` | Statement parsing, ratio derivation, FMP vs yfinance paths |
| `test_sec_data.py` | Form 4 XML parsing, CIK map, issuer check, insider rebuild, cluster-buy detection, lookback filtering |
| `test_institutional.py` | 13-F filing selection, parsing, OpenFIGI/name resolution, summary rebuild, point-in-time loader |
| `test_earnings_calendar.py` | Earnings dates from `.info`, `0q` EPS estimate, failed vs empty fetch, superseded dates |
| `test_yahoo_info.py` | Yahoo retry/backoff, shared `.info` and `earningsTrend` per run |
| `test_estimates.py` | `estimate_trends` rows from the shared `earningsTrend` |
| `test_snapshot_coverage.py` | Snapshot second pass and coverage ERROR |
| `test_edgar_docs.py` | EDGAR index parsing, HTML-to-markdown table conversion |
| `test_providers.py` | Provider selection based on env var presence |

**Layer 2 — Factor scoring**

| File | Subject |
|------|---------|
| `test_momentum.py` | 12-1m return, acceleration, relative strength sub-factors |
| `test_value.py` | Forward yield (guarded `+1y` trend), FCF yield, EV/EBITDA, shareholder yield sub-factors |
| `test_value_inputs.py` | Market cap from snapshots, TTM flows, one balance-sheet quarter, operating income, equity rules (packet 42) |
| `test_market_caps.py` | `market_caps` ingestion, effective shares and the glitch guard |
| `test_quality.py` | ROE stability, Piotroski F-Score, Altman Z-Score, accruals |
| `test_growth.py` | Revenue/earnings YoY, FCF growth, R&D intensity |
| `test_revisions.py` | 30/60/90-day percent revisions from the `+1y` trend, glitch guard, fiscal-year roll-over |
| `test_short_interest.py` | Short % float, days-to-cover, 30-day change |
| `test_insider.py` | Net dollar flow, CEO/CFO weighting, cluster flag ranking |
| `test_factor_institutional.py` | Fund count, net share change, simultaneous new positions |
| `test_loader.py` | Data loading from Layer 1 tables |
| `test_composite.py` | Sector percentile ranking, weighted blend |
| `test_regime_weights.py` | VIX-conditional weight adjustment |
| `test_crowding.py` | Rolling pairwise factor-return correlation detection |

**Layer 3 — AI analysis**

| File | Subject |
|------|---------|
| `test_api_client.py` | OpenAI wrapper, retry logic, JSON mode, cost guard |
| `test_llm.py` | Provider selection (OpenAI / OpenRouter), overrides, model resolution |
| `test_llm_call_sites.py` | LLM wiring for letter, commentary and JARVIS |
| `test_doc_summary.py` | Filing summary prompts, token cut, figure check, output cleaning |
| `test_cost_tracker.py` | Token and USD cost accounting per model |
| `test_analysis_cache.py` | PostgreSQL result cache, TTL expiry, artifact key collisions |
| `test_insider_analyzer.py` | Signal strength enum, key transaction parsing |
| `test_report_generator.py` | Combined report assembly |
| `test_combined_score.py` | 60/40 quant/AI blend, fallback to pure quant |

**Layer 4 — Portfolio construction**

| File | Subject |
|------|---------|
| `test_optimizer.py` | Conviction-tilt algorithm, tilt bounds, earnings haircut |
| `test_mvo_optimizer.py` | Markowitz MVO, SLSQP convergence, fallback logic |
| `test_rebalance.py` | Trade list generation, turnover budget enforcement |
| `test_rebalance_schedule.py` | Earnings, FOMC, OpEx blackout windows |
| `test_beta.py` | Rolling beta vs SPY, net portfolio beta |
| `test_transaction_costs.py` | Slippage model, ADV-based liquidity cap |
| `test_state.py` | Position state persistence and round-trip |

**Layer 5 — Risk management**

| File | Subject |
|------|---------|
| `test_risk_pre_trade.py` | All 8 pre-trade veto checks |
| `test_risk_circuit_breakers.py` | Daily/weekly loss and drawdown triggers |
| `test_risk_tail_risk.py` | VIX and credit spread monitors |
| `test_risk_factor_risk.py` | Barra-style variance decomposition, MCTR |
| `test_risk_stress_test.py` | Historical and synthetic stress scenarios |
| `test_risk_correlation.py` | Same-book pairwise correlation check |
| `test_risk_state.py` | Risk state JSON cache read/write |

**Layer 6 — Execution**

| File | Subject |
|------|---------|
| `test_execution_broker.py` | Alpaca client, position sync, market clock |
| `test_execution_executor.py` | Order submission, fill polling, partial fills |
| `test_execution_order_manager.py` | SIGINT handler, cancel-open-orders |
| `test_execution_short_check.py` | Shortability cache (7-day TTL) |
| `test_execution_costs.py` | Slippage statistics |
| `test_execution_db.py` | `execution_orders` table schema |

**Layer 7 — Reporting**

| File | Subject |
|------|---------|
| `test_reporting_nav.py` | NAV series, drawdown from portfolio_history |
| `test_reporting_attribution.py` | Beta/sector/factor/alpha P&L decomposition |
| `test_reporting_fifo.py` | FIFO round-trip position attribution |
| `test_reporting_win_loss.py` | Win rate, streaks, breakdowns by side and sector |

**Dashboard**

| File | Subject |
|------|---------|
| `test_page_ticker.py` | Ticker page queries and whole-page render tests (Streamlit `AppTest`), including the filing viewer |
| `test_ticker_institutional.py` | Per-fund holdings table and summary formatting |
| `test_ticker_signals.py` | Signal, sub-factor table, trend charts, insider clusters |
| `test_ticker_research.py` | AI analysis rendering and escaping, filing links |
| `test_ticker_valuation.py` | Market cap, EV, trailing multiples and yields |

Run `make test` for the current count.

### 6.3 Upsert compatibility

The `insert_or_replace` and `insert_or_ignore` helpers resolve to SQLite dialect insert objects in tests (via the `on_conflict_do_*` API), ensuring that upsert semantics are exercised without requiring PostgreSQL.

---

## 7. Implemented Layer Architecture

### Layer 2 — Factor Scoring (`factors/`)

Stops with an error, before loading data or writing scores, unless SPY has a bar with a close dated the score date (stale prices or a market holiday). Reads Layer 1 tables; computes 27 sub-factors across 8 dimensions for every S&P 500 stock. All scores are sector-percentile ranks (0–100). Composite weights are adjusted by market regime (VIX-based LOW/NORMAL/HIGH_VOL). Crowding flags are written after 60 days of history. Output: `factor_scores`, `regime_state`, `crowding_flags`.

### Layer 3 — AI Analysis (`analysis/`)

Calls the configured LLM (OpenAI models, directly or via OpenRouter) for LONG/SHORT candidates identified by Layer 2. Analyses earnings transcripts, 10-K/10-Q filings, and insider transactions. Results are cached in PostgreSQL with a 30-day TTL. Combined score: 60% Layer 2 quantitative + 40% AI normalised. Cost ceiling: $25/run. Output: `analysis_results`, `ai_scores`.

### Layer 4 — Portfolio Construction (`portfolio/`)

Consumes Layer 3 `combined_scores` to build a 20-long / 20-short book against the NAV in `portfolio.nav_usd` (set to $100,000 in `config.yaml`). Two methods: conviction-tilt (default, always converges) and Markowitz MVO (optional, falls back to conviction-tilt on non-convergence). Constraints: 150% gross, 0–10% net, 5% single-name cap, 25% sector gross cap, 0.15 net beta. Output: `portfolio_positions`, `portfolio_history`, `position_approvals` (PENDING).

### Layer 5 — Risk Management (`risk/`)

Post-optimisation, pre-execution gate. Eight pre-trade veto checks stamp each PENDING approval APPROVED or REJECTED. Circuit breakers monitor intraday and weekly P&L (halt lock at 8% drawdown). Tail-risk monitors track VIX and credit spreads. Barra-style factor risk model decomposes portfolio variance; MCTR is computed per position. Stress tests cover 2008 crisis, COVID crash, 2022 rate hike, and synthetic scenarios. Output: updated `position_approvals`, `risk_log`, `risk_events`, `cache/risk_state.json`.

### Layer 6 — Execution (`execution/`)

Reads APPROVED rows from `position_approvals`; submits day-limit orders via the Alpaca API with a 0.5% price buffer. Large orders are chunked to ≤2% ADV. Short positions are gated by a 7-day shortability cache. Fills are polled until terminal; live positions are reconciled after each run. Output: `execution_orders`, updated `portfolio_positions` and `portfolio_history`.

### Layer 7 — Reporting and Dashboard (`reporting/`, `dashboard/`)

**Reporting engine** (`run_reporting.py`): nightly P&L decomposition (beta + sector Brinson + factor OLS + alpha), FIFO position attribution, win/loss analysis, tear sheets, AI-generated weekly commentary, and LP letters. Output: `pnl_attribution`, `portfolio_nav`, `position_trades`, `lp_letters`, `weekly_commentary`.

**Streamlit dashboard** (`dashboard/app.py`): seven pages served at `http://localhost:8502` — Portfolio, Research, Risk, Performance, Execution, Letter, Ticker. The Ticker page (`dashboard/ticker_*.py`) shows a ticker's valuation, signal, factor and AI scores, institutional holdings, and SEC filings with an exact EDGAR reader and saved AI summaries. Auto-refreshes every 5 minutes during market hours. The JARVIS assistant uses the configured LLM. See `docs/run_dashboard.md` for what the dashboard writes or calls.

---

## 8. Configuration and Secrets Management

`config.yaml` holds all non-secret runtime parameters. Secrets (API keys, database credentials) are read exclusively from environment variables, loaded from `.env` via `python-dotenv` at process startup. The `.env` file is gitignored. `.env.example` documents every expected variable without values.

The database URL is the one exception to `config.yaml`: it is read only from `DATABASE_URL`, so credentials never enter the repository.
