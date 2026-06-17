# Meridian Capital Partners — Layer 1: Data Ingestion

Build Layer 1 of a long/short equity hedge fund system called **"Meridian Capital Partners."**  
Project folder: `ls_equity_fund`  
This layer handles **ALL data ingestion** — no scoring, no analysis — just pulling data from 5 sources into a local SQLite database.

---

## Project Structure

| Directory / File | Purpose |
|------------------|---------|
| `data/` | This layer |
| `factors/` | Layer 2 (scoring) |
| `analysis/` | Layer 3 (Claude AI) |
| `portfolio/` | Layer 4 (construction) |
| `risk/` | Layer 5 (risk management) |
| `execution/` | Layer 6 (Alpaca) |
| `reporting/` | Layer 7 (reports) |
| `dashboard/` | Layer 7 (Streamlit) |
| `cache/` | SQLite + cached files |
| `output/` | CSVs, logs, reports |
| `config.yaml` | All parameters |
| `.env` | API keys (gitignored) |

---

## 5 Data Sources

### 1. Universe (`data/universe.py`)

Scrape current S&P 500 list from Wikipedia. Store ticker, company name, GICS sector, sub-industry. Cache locally, refresh weekly.

Also maintain benchmark tickers:
- **Broad market:** SPY, QQQ, IWM, DIA
- **Sector ETFs:** XLK, XLF, XLV, XLE, XLI, XLC, XLY, XLP, XLB, XLRE, XLU
- **Other:** ^VIX, TLT, HYG

---

### 2. Market Data + Fundamentals (`data/market_data.py` + `data/fundamentals.py`)

**Market Data:**
- Daily OHLCV via `yfinance` for all universe + benchmarks
- 3-year lookback
- Incremental updates — only fetch new data since last stored date
- SQLite table: `daily_prices`

**Fundamentals:**
- Quarterly + annual income statement, balance sheet, cash flow via `yfinance`
- Calculate **24 derived ratios:**

| Ratio | Ratio | Ratio |
|-------|-------|-------|
| ROE | ROA | Gross margin |
| Operating margin | Net margin | Revenue growth YoY |
| Revenue growth QoQ | Earnings growth YoY | Earnings growth QoQ |
| Debt/equity | FCF yield | Current ratio |
| AR/revenue | CFO/NI | Accruals ratio |
| Retained earnings | Working capital | Total liabilities |
| EBIT | R&D expense | Shares outstanding |
| Dividends paid | Buybacks | Asset turnover |

---

### 3. SEC Filing Data (`data/sec_data.py`)

Connect to SEC EDGAR EFTS API. Headers: User-Agent with email, 8 req/sec rate limit.

For each ticker fetch:
- Latest 10-K (full doc for Risk Factors)
- Latest 10-Q (MD&A)
- Recent 8-K filings
- Form 4 insider transactions (last 180 days)

**Form 4 parsing** — into `insider_transactions` table:  
`ticker`, `insider_name`, `insider_title`, `transaction_type`, `transaction_code`, `shares`, `price`, `date`, `ownership_type`

- Distinguish open-market purchases (code `P`) from grants/exercises (`A`, `M`, `F`)
- Flag CEO/CFO purchases
- Flag cluster buying (3+ insiders within 30 days, same ticker)
- Add `--no-filings` flag to skip SEC for fast daily runs
- Add `--forms` flag for selective pulls

---

### 4. Institutional Holdings (`data/institutional.py`)

Fetch 13-F filings from SEC EDGAR for 9 hedge funds:  
Citadel, Point72, Bridgewater, Tiger Global, Third Point, Berkshire Hathaway, Appaloosa, Baupost, Pershing Square.

Parse: `fund_name`, `ticker`, `shares_held`, `market_value`, `report_date`

- Calculate per ticker: number of tracked funds holding, net change from prior quarter
- Flag tickers with 3+ funds opening new positions simultaneously
- Add `--no-13f` flag for fast daily runs

---

### 5. Short Interest (`data/short_interest.py`)

Fetch from `yfinance .info`: `shares_short`, `short_ratio`, `short_percent_of_float`.  
Daily snapshots in SQLite table `short_interest`. Refresh daily.

---

### 6. Analyst Estimates (`data/estimates.py`)

Fetch forward EPS estimate and price target consensus via `yfinance`. Store as daily snapshots in `analyst_estimates` table.  
Revisions factor needs 30+ days of snapshots to compute 30/60/90-day deltas. Refresh daily.

---

### 7. Earnings Calendar (`data/earnings_calendar.py`)

Fetch upcoming earnings dates for next 30 days across the universe. Refresh daily.

---

### 8. Earnings Transcripts (`data/transcripts.py`)

If `FMP_API_KEY` in `.env`, fetch latest transcript from Financial Modeling Prep API:  
`https://financialmodelingprep.com/api/v3/earning_call_transcript/{SYMBOL}`

- Store in `earnings_transcripts` table
- Only fetch for long/short candidates, not entire universe
- If no FMP key, skip gracefully and log

---

### 9. Provider Abstraction (`data/providers.py`)

Create a provider layer that routes to the best available data source:

| API Key in `.env` | Provider Used |
|-------------------|---------------|
| `POLYGON_API_KEY` | Polygon for daily prices (licensed exchange data) |
| `FMP_API_KEY` | FMP for transcripts + structured financials |
| `FRED_API_KEY` | FRED for yield curve, credit spread, fed funds rate |
| *(none)* | yfinance for prices/fundamentals, SEC EDGAR for filings |

Log which provider is active: e.g. `"Using Polygon for prices"` or `"Falling back to yfinance"`

---

## Entry Point

### `run_data.py`

**Arguments:** `--no-filings`, `--no-13f`

**Execution order:**

```
universe → prices → fundamentals → short interest → estimates →
earnings calendar → transcripts → SEC filings (unless --no-filings) →
13-F (unless --no-13f)
```

- Log everything to `output/run.log`
- Print summary: tickers updated, price bars added, filings cached, insider txns parsed

