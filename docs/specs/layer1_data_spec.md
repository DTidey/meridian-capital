# Meridian Capital Partners — Layer 1: Data Ingestion
## Implementation Specification

**Date:** 2026-05-06  
**Status:** Complete  
**Depends on:** PostgreSQL 16 (Docker) — `docker-compose.yml` at project root

---

## 1. Overview

Layer 1 is the data foundation for the entire system. It populates a PostgreSQL database with everything the scoring, analysis, portfolio, risk, and execution layers need:

- S&P 500 universe + 18 benchmark/ETF tickers
- 3 years of daily OHLCV prices (incremental updates)
- Quarterly fundamentals with 24 derived ratios
- SEC EDGAR filings: 10-K, 10-Q, 8-K, Form 4 insider transactions
- 13-F institutional holdings from 9 tracked funds
- Short interest, analyst estimates, earnings calendar — daily snapshots
- Earnings transcripts (FMP API)

All data is stored in PostgreSQL under a shared SQLAlchemy metadata object (`data.db.metadata`). Every other layer registers its tables on the same metadata, so a single `initialise_schema(engine)` call creates the full schema.

---

## 2. Module Structure

```
ls_equity_fund/
├── run_data.py             # Layer 1 entry point
└── data/
    ├── __init__.py
    ├── db.py               # Shared metadata, table definitions, engine factory
    ├── providers.py        # API provider selection (yfinance vs Polygon/FMP)
    ├── universe.py         # S&P 500 universe from Wikipedia
    ├── market_data.py      # Daily OHLCV prices
    ├── fundamentals.py     # Quarterly financial statements
    ├── sec_data.py         # SEC EDGAR: filings, Form 4 insider transactions
    ├── institutional.py    # 13-F institutional holdings
    ├── short_interest.py   # Short interest daily snapshots
    ├── estimates.py        # Analyst EPS estimates daily snapshots
    ├── earnings_calendar.py# Upcoming earnings dates
    └── transcripts.py      # Earnings call transcripts (FMP)
```

---

## 3. Database

### 3.1 Connection

Primary: PostgreSQL 16 via Docker.  
Connection string: `postgresql+psycopg2://meridian:meridian@localhost:5432/meridian`  
Override: `DATABASE_URL` environment variable (useful in containers / CI).

SQLite (`:memory:`) is used in all tests via the same `initialise_schema` path.

### 3.2 Engine factory (`data/db.py`)

```python
def get_engine(url: str | dict) -> sa.engine.Engine
```

- Accepts either a URL string or the `config["database"]` dict.
- For SQLite: enables WAL journal mode and foreign keys via `PRAGMA` on connect.
- For SQLite file databases: creates parent directories automatically.

### 3.3 Schema initialisation

```python
def initialise_schema(engine: sa.engine.Engine) -> None
```

Calls `metadata.create_all(engine, checkfirst=True)`. Because all layer tables are registered on the same `metadata` object (via module-level imports), this creates every table across all layers in one call.

### 3.4 Upsert helpers

```python
def insert_or_replace(conn, table) -> sa.sql.Insert
def insert_or_ignore(conn, table) -> sa.sql.Insert
```

Both return dialect-aware `INSERT … ON CONFLICT` statements — PostgreSQL dialect uses `sqlalchemy.dialects.postgresql.insert`; SQLite uses `sqlalchemy.dialects.sqlite.insert`. This allows tests to use SQLite without any code changes.

---

## 4. Table Definitions

All tables are defined in `data/db.py` and registered on `metadata`.

### `sp500_universe`
| Column | Type | Notes |
|---|---|---|
| ticker | String PK | e.g. `AAPL` |
| company_name | String | |
| gics_sector | String | 11 GICS sectors |
| gics_sub_industry | String | |
| updated_at | String | ISO timestamp |

### `benchmark_tickers`
| Column | Type | Notes |
|---|---|---|
| ticker | String PK | e.g. `SPY`, `^VIX` |
| category | String | `broad_market` / `sector_etfs` / `other` |

### `daily_prices`
PK: `(ticker, date)`  
Indexes: `ticker`, `date`

| Column | Type |
|---|---|
| ticker | String |
| date | String (ISO date) |
| open, high, low, close | Float |
| adj_close | Float |
| volume | Integer |

### `fundamentals`
PK: `(ticker, period_type, period_end)`  
Index: `ticker`

Stores raw statement fields plus 24 pre-computed ratios. Period types: `annual` / `quarterly`.

**Raw fields:** revenue, gross_profit, operating_income, ebit, net_income, rd_expense, total_assets, total_liabilities, total_equity, cash, total_debt, current_assets, current_liabilities, accounts_receivable, retained_earnings, shares_outstanding, dividends_paid, cfo, capex, fcf, buybacks

**Derived ratios (stored):** roe, roa, gross_margin, operating_margin, net_margin, revenue_growth_yoy, revenue_growth_qoq, earnings_growth_yoy, earnings_growth_qoq, debt_to_equity, fcf_yield, current_ratio, ar_to_revenue, cfo_to_ni, accruals_ratio, working_capital, asset_turnover

### `sec_filings`
PK: `id` (autoincrement). Unique: `accession_no`.

| Column | Type |
|---|---|
| ticker | String |
| form_type | String (`10-K`, `10-Q`, `8-K`, `4`) |
| filed_date | String |
| accession_no | String |
| filing_url | String |
| content_text | Text |
| fetched_at | String |

### `insider_transactions`
PK: `id`. Unique: `(ticker, accession_no, insider_name, date, shares)`

| Column | Type | Notes |
|---|---|---|
| ticker | String | |
| insider_name | String | |
| insider_title | String | |
| transaction_type | String | |
| transaction_code | String | `P`, `S`, `A`, `M`, `F` |
| shares | Float | |
| price | Float | |
| date | String | |
| ownership_type | String | `D` (direct) / `I` (indirect) |
| is_open_market | Integer | 1 for codes P and S only |
| is_ceo_cfo | Integer | 1 if title matches CEO/CFO |
| accession_no | String | |
| fetched_at | String | |

### `insider_cluster_flags`
PK: `(ticker, window_start)`

Flagged when ≥ 3 insiders buy within a 30-day window.

| Column | Type |
|---|---|
| ticker | String |
| window_start | String |
| window_end | String |
| insider_count | Integer |
| total_shares | Float |
| flagged_at | String |

### `institutional_holdings`
Unique: `(fund_name, ticker, report_date)`

| Column | Type |
|---|---|
| fund_name | String |
| ticker | String |
| shares_held | Float |
| market_value | Float |
| report_date | String |
| fetched_at | String |

### `institutional_summary`
PK: `(ticker, report_date)` — aggregated across all tracked funds.

| Column | Type |
|---|---|
| ticker | String |
| report_date | String |
| funds_holding | Integer |
| net_share_change | Float |
| new_positions | Integer |

### `short_interest`
PK: `(ticker, date)` — daily snapshot.

| Column | Type |
|---|---|
| ticker | String |
| date | String |
| shares_short | Float |
| short_ratio | Float (days to cover) |
| short_pct_float | Float |
| fetched_at | String |

### `analyst_estimates`
PK: `(ticker, date)` — daily snapshot.

| Column | Type |
|---|---|
| ticker | String |
| date | String |
| eps_estimate_fwd | Float |
| price_target | Float |
| num_analysts | Integer |
| fetched_at | String |

### `earnings_calendar`
PK: `(ticker, earnings_date)`

| Column | Type |
|---|---|
| ticker | String |
| earnings_date | String |
| eps_estimate | Float |
| fetched_at | String |

### `earnings_transcripts`
Unique: `(ticker, earnings_date)`

| Column | Type |
|---|---|
| ticker | String |
| earnings_date | String |
| quarter | String (e.g. `Q1`) |
| year | Integer |
| content | Text (full transcript) |
| fetched_at | String |

---

## 5. Data Sources

### 5.1 Universe (`data/universe.py`)

S&P 500 constituents scraped from Wikipedia (HTML table parse via `pandas.read_html`). Cache TTL: 7 days (`config.universe.cache_refresh_days`). Stored in `sp500_universe`.

18 benchmark/ETF tickers added separately to `benchmark_tickers`:
- Broad market: SPY, QQQ, IWM, DIA
- Sector ETFs: XLK, XLF, XLV, XLE, XLI, XLC, XLY, XLP, XLB, XLRE, XLU
- Other: ^VIX, TLT, HYG

`get_all_tickers(conn, config) -> list[str]` returns the combined deduplicated list. Tickers containing `.` (e.g. `BRK.B`) are normalised to `-` for yfinance compatibility.

### 5.2 Prices (`data/market_data.py`)

**Default:** yfinance — batched downloads of 100 tickers per call (`yf.download`), falling back to single-ticker `yf.Ticker.history` on batch failure.

**Polygon alternative:** activated when `POLYGON_API_KEY` is set in environment. Fetches per-ticker from `api.polygon.io/v2/aggs/`.

**Incremental updates:** `_last_stored_date(conn, ticker)` queries the max date stored; only requests bars from that date forward. Lookback: 3 years (`config.market_data.lookback_years`).

**Upsert:** `insert_or_replace` — re-running is safe.

### 5.3 Fundamentals (`data/fundamentals.py`)

**Default:** yfinance `Ticker.financials`, `Ticker.balance_sheet`, `Ticker.cashflow` — quarterly and annual.

**FMP alternative:** activated when `FMP_API_KEY` is set. Fetches from `financialmodelingprep.com/api/v3/`.

24 derived ratios computed in-process and stored alongside raw fields to avoid re-computation in later layers:
- Margins: gross_margin, operating_margin, net_margin
- Returns: roe (`net_income / equity`), roa (`net_income / assets`)
- Growth: revenue_growth_yoy, revenue_growth_qoq, earnings_growth_yoy, earnings_growth_qoq
- Leverage: debt_to_equity
- Cash: fcf_yield (`fcf / market_cap` — requires latest price), cfo_to_ni, accruals_ratio
- Efficiency: current_ratio, ar_to_revenue, asset_turnover, working_capital
- Capital returns: (included in buybacks and dividends_paid columns)

### 5.4 SEC EDGAR (`data/sec_data.py`)

Fetches 10-K, 10-Q, 8-K, and Form 4 filings from SEC EDGAR.

**Rate limiting:** 8 requests/second (SEC fair-use limit of 10, with margin). Implemented with a `_RateLimiter` class using `time.monotonic()`.

**User-Agent header:** required by SEC. Set from `SEC_USER_AGENT` and `SEC_USER_EMAIL` environment variables.

**CIK lookup:** ticker → CIK via `sec.gov/cgi-bin/browse-edgar`. Results cached in-memory for the run duration.

**Form 4 (insider transactions):** parsed to extract:
- Transaction code (`P` = open-market purchase, `S` = sale, etc.)
- `is_open_market`: 1 for codes P and S
- `is_ceo_cfo`: 1 if insider title contains CEO or CFO
- Cluster flag: raised when ≥ 3 insiders buy within 30 days (`config.sec.cluster_buy_min_insiders`)

**Form lookback windows** (`config.sec.form_lookback_days`):
| Form | Default lookback |
|---|---|
| 10-K | 400 days (latest annual + one prior) |
| 10-Q | 270 days (~3 quarters) |
| 8-K | 90 days (recent material events) |
| 4 | 180 days |

### 5.5 Institutional Holdings (`data/institutional.py`)

9 tracked funds (`config.institutional.tracked_funds`): Citadel, Point72, Bridgewater, Tiger Global, Third Point, Berkshire Hathaway, Appaloosa, Baupost, Pershing Square.

Fetched from SEC EDGAR 13-F filings (CIK lookup → latest submission → XML parse). After per-fund rows are stored in `institutional_holdings`, the module aggregates into `institutional_summary` (funds_holding count, net_share_change, new_positions count).

`new_positions_flag_count`: a ticker is flagged for "simultaneous open" when ≥ 3 funds initiate new positions in the same report period (`config.institutional.new_position_flag_count`).

### 5.6 Short Interest (`data/short_interest.py`)

Daily snapshot via yfinance `Ticker.info` fields: `sharesShort`, `shortRatio`, `shortPercentOfFloat`. Stored as a time series snapshot — each daily run appends a row with today's date if not already present.

### 5.7 Analyst Estimates (`data/estimates.py`)

Daily snapshot via yfinance `Ticker.info` fields: `forwardEps`, `targetMeanPrice`, `numberOfAnalystOpinions`. Same time-series snapshot pattern as short interest.

### 5.8 Earnings Calendar (`data/earnings_calendar.py`)

Lookahead: 30 days (`config.earnings_calendar.lookahead_days`). Fetched via yfinance `Ticker.calendar`. Upserted by `(ticker, earnings_date)`.

### 5.9 Transcripts (`data/transcripts.py`)

FMP API (`FMP_API_KEY` required). Endpoint: `financialmodelingprep.com/api/v3/earning_call_transcript/{ticker}`. Stored as full text in `earnings_transcripts`. Only fetched for LONG/SHORT candidates passed by the caller — not run on the full universe.

---

## 6. Provider Selection (`data/providers.py`)

```python
class Providers:
    prices: PriceProvider      # YFINANCE (default) or POLYGON
    fundamentals: FundProvider # YFINANCE (default) or FMP
    polygon_key: str | None
    fmp_key: str | None
```

Provider is selected at startup based on which API keys are present in the environment. All modules accept a `Providers` instance so the selection logic is centralised.

---

## 7. Entry Point (`run_data.py`)

```
python run_data.py [--no-filings] [--no-13f] [--forms 10-K 10-Q]
                   [--force-universe] [--tickers AAPL MSFT] [--verbose]
```

| Flag | Behaviour |
|---|---|
| *(no flags)* | Full run — all 8 steps for the full universe |
| `--no-filings` | Skip SEC EDGAR (steps 8a) — typical fast daily run |
| `--no-13f` | Skip 13-F institutional (step 8b) — typical fast daily run |
| `--forms 4 10-K` | Only fetch specified SEC form types |
| `--force-universe` | Ignore Wikipedia cache TTL and re-scrape |
| `--tickers AAPL MSFT` | Scope to specific tickers (development / debugging) |

**Execution sequence:**
```
1. Load config + connect to database + initialise schema
2. Resolve universe  →  sp500_universe, benchmark_tickers
3. Fetch prices      →  daily_prices
4. Fetch fundamentals→  fundamentals
5. Short interest    →  short_interest
6. Analyst estimates →  analyst_estimates
7. Earnings calendar →  earnings_calendar
8. Transcripts       →  earnings_transcripts  (LONG/SHORT candidates only)
8a. SEC filings      →  sec_filings, insider_transactions, insider_cluster_flags
8b. 13-F holdings    →  institutional_holdings, institutional_summary
```

**Summary output:**
```
=== Layer 1 Data Ingestion Complete ===
S&P 500 tickers              503
Total ticker universe        521
Price bars added             8 204
Tickers with new prices      503
Fundamental periods added    1 012
Short interest updated       503 tickers
Estimates updated            503 tickers
Upcoming earnings events     47
Transcripts stored           0
SEC filings cached           1 234
Insider transactions         892
Institutional holdings       4 107
Elapsed                      142.3s
```

---

## 8. Configuration (`config.yaml`)

```yaml
database:
  url: postgresql+psycopg2://meridian:meridian@localhost:5432/meridian

universe:
  wikipedia_url: "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
  cache_refresh_days: 7
  benchmark_tickers:
    broad_market: [SPY, QQQ, IWM, DIA]
    sector_etfs:  [XLK, XLF, XLV, XLE, XLI, XLC, XLY, XLP, XLB, XLRE, XLU]
    other:        ["^VIX", TLT, HYG]

market_data:
  lookback_years: 3
  price_table: daily_prices

sec:
  rate_limit_per_sec: 8
  insider_lookback_days: 180
  cluster_buy_window_days: 30
  cluster_buy_min_insiders: 3
  form_lookback_days:
    "10-K": 400
    "10-Q": 270
    "8-K":  90
    "4":    180
  forms: ["10-K", "10-Q", "8-K", "4"]

institutional:
  tracked_funds:
    - {name: "Citadel Advisors",          cik: "0001423053"}
    - {name: "Point72 Asset Management",  cik: "0001603466"}
    - {name: "Bridgewater Associates",    cik: "0001350694"}
    - {name: "Tiger Global Management",  cik: "0001167483"}
    - {name: "Third Point",               cik: "0001040570"}
    - {name: "Berkshire Hathaway",        cik: "0001067983"}
    - {name: "Appaloosa Management",      cik: "0001070154"}
    - {name: "Baupost Group",             cik: "0001061768"}
    - {name: "Pershing Square Capital",   cik: "0001336528"}
  new_position_flag_count: 3

earnings_calendar:
  lookahead_days: 30

transcripts:
  fmp_base_url: "https://financialmodelingprep.com/api/v3"

logging:
  log_file: output/run.log
  level: INFO
```

---

## 9. Environment Variables

```
# Required for PostgreSQL
DATABASE_URL=postgresql+psycopg2://meridian:meridian@localhost:5432/meridian

# Optional — unlock premium data sources
POLYGON_API_KEY=your_key       # replaces yfinance for prices
FMP_API_KEY=your_key           # replaces yfinance for fundamentals + transcripts

# SEC EDGAR (required by SEC if making many requests)
SEC_USER_AGENT=MeridianCapital/1.0
SEC_USER_EMAIL=you@example.com

# Alpaca (Layer 6 — not used here)
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
ALPACA_PAPER=true
```

---

## 10. Key Design Decisions

| Decision | Rationale |
|---|---|
| Shared `metadata` object | All layer tables created in one `initialise_schema` call; no per-layer DDL step |
| Dialect-aware upsert helpers | Tests use SQLite; production uses PostgreSQL — same code, no branches in business logic |
| Derived ratios stored at ingestion time | Avoid re-computation in every downstream layer; 24 ratios cover all Layer 2 factor needs |
| `is_open_market` / `is_ceo_cfo` flags pre-computed | Form 4 parsing can be slow; Layer 2 insider scoring just filters on the flag |
| Incremental price updates | `max(date)` query per ticker avoids re-downloading 3 years of history each day |
| Rate limiter as a class (not `time.sleep`)  | Testable; monotonic clock avoids wall-clock skew |
| `DATABASE_URL` env override | Allows the same codebase to run against local SQLite (tests), Docker PostgreSQL (dev), and managed PostgreSQL (prod) without config changes |
