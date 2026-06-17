# Meridian Capital Partners

A quantitative long/short equity fund system for the S&P 500 universe. The pipeline ingests market, fundamental, and alternative data; scores securities across eight factor dimensions; applies AI-assisted analysis; constructs a market-neutral portfolio; manages risk; routes orders through Alpaca; and publishes daily reports via a Streamlit dashboard.

## Architecture

The system is structured as seven sequential layers, each with its own entry point:

| Layer | Script | Purpose |
|---|---|---|
| 1 | `run_data.py` | Data ingestion — prices, fundamentals, SEC filings, transcripts, institutional 13-F holdings |
| 2 | `run_scoring.py` | Factor scoring across 8 dimensions with regime-conditional weighting |
| — | `run_transcripts.py` | Earnings transcript ingestion (run between Layers 2 and 3) |
| 3 | `run_analysis.py` | AI analysis via OpenAI — earnings, filings, risk, insider signals |
| 4 | `run_portfolio.py` | Portfolio construction — MVO optimizer, conviction weighting, rebalance scheduling |
| 5 | `run_risk_check.py` | Risk management — pre-trade veto, circuit breakers, tail risk, stress tests |
| 6 | `run_execution.py` | Order execution via Alpaca brokerage API |
| 7 | `run_reporting.py` | Reporting — tear sheet, P&L attribution, LP letter, factor decomposition |

Run all layers in sequence with `python run_all.py`.

The Streamlit dashboard (`dashboard/app.py`) provides a live view of portfolio positions, research candidates, risk metrics, performance attribution, execution status, and the LP letter.

## Factor Model

Eight factors are scored 0–100 for each ticker and blended into a composite score. The default weights (configurable in `config.yaml`) are:

| Factor | Default weight |
|---|---|
| Momentum | 20% |
| Quality | 20% |
| Value | 15% |
| Estimate revisions | 15% |
| Insider activity | 10% |
| Growth | 10% |
| Short interest | 5% |
| Institutional positioning | 5% |

Weights shift automatically based on the VIX regime (low vol / normal / high vol). The composite score is blended 60/40 with OpenAI-generated AI scores from earnings transcripts and SEC filings.

## Portfolio Construction

- 20 longs / 20 shorts from the S&P 500 universe
- Gross exposure target: 150% (90% long / 60% short)
- Net exposure constrained to 0–10%
- Max position: 5% of NAV; max sector: 25%
- Net portfolio beta capped at 0.15
- Mean-variance optimisation (MVO) with conviction tilts for top-5 and top-10 ranked names
- Turnover budget: 30% per rebalance

## Prerequisites

- Python 3.12
- Docker (for PostgreSQL)
- An [Alpaca](https://alpaca.markets/) account (paper or live)
- An [OpenAI](https://platform.openai.com/) API key
- Optional: [Financial Modeling Prep](https://financialmodelingprep.com/) key for earnings transcripts; [FRED](https://fred.stlouisfed.org/) key for macro tail-risk data

## Setup

**1. Start the database**

```bash
docker compose up -d
```

This starts PostgreSQL 16 on port 5432 with a persistent named volume (`meridian_postgres_data`). The schema is created automatically on first run.

**2. Configure environment**

```bash
cp .env.example .env
```

Edit `.env` and fill in:

```
DATABASE_URL=postgresql+psycopg2://meridian:meridian@localhost:5432/meridian
OPENAI_API_KEY=sk-...
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
ALPACA_PAPER=true
SEC_USER_AGENT=YourName/1.0
SEC_USER_EMAIL=you@example.com

# Optional
FMP_API_KEY=...
FRED_API_KEY=...
POLYGON_API_KEY=...
```

**3. Create the Python environment**

```bash
make venv
make sync
```

## Running the pipeline

Full pipeline (all seven layers in sequence):

```bash
. .venv/bin/activate && python run_all.py
```

Individual layers:

```bash
python run_data.py              # Layer 1 — data ingestion
python run_data.py --no-filings --no-13f   # fast daily run, skip SEC
python run_scoring.py           # Layer 2 — factor scoring
python run_transcripts.py       # transcript ingestion
python run_analysis.py          # Layer 3 — AI analysis
python run_portfolio.py         # Layer 4 — portfolio construction
python run_risk_check.py        # Layer 5 — risk checks
python run_execution.py         # Layer 6 — order execution
python run_reporting.py         # Layer 7 — reporting
```

## Dashboard

```bash
. .venv/bin/activate && streamlit run dashboard/app.py --server.port 8502
```

Open `http://localhost:8502`. The dashboard auto-refreshes every 5 minutes during market hours (9:30–16:00 ET on weekdays).

## Data sources

| Source | Data |
|---|---|
| yfinance | Prices (3yr lookback), fundamentals, short interest, analyst estimates, earnings calendar |
| SEC EDGAR | 10-K, 10-Q, 8-K, Form 4 insider transactions (8 req/s rate limit) |
| 13-F filings | Institutional holdings for 9 tracked funds (Citadel, Point72, Berkshire, etc.) |
| FMP API | Earnings transcripts (optional; falls back to SEC 8-K Exhibit 99 mining) |
| Polygon.io | Price data alternative (optional; falls back to yfinance) |
| FRED | Macro indicators for tail-risk monitoring (optional) |
| Alpaca | Order execution and shortability checks |
| OpenAI | GPT-4o for earnings and filing analysis; GPT-4o-mini for lower-cost tasks |

## Development

```bash
make lint        # ruff check + format
make test        # pytest (528 tests)
make security    # bandit + pip-audit
make precommit   # all pre-commit hooks
```

Run a single test:

```bash
. .venv/bin/activate && pytest tests/test_risk_pre_trade.py::TestPositionSizingVeto -q
```

Dependencies are managed with pip-tools. To add or change a dependency:

```bash
# Edit requirements.in (runtime) or requirements-dev.in (dev)
make compile     # regenerate requirements.txt / requirements-dev.txt
make sync        # install into .venv
```

## CI

GitHub Actions runs on every push and pull request:

- **CI** (`ci.yml`) — lint, security scan (bandit + pip-audit), and full test suite
- **CodeQL** (`codeql.yml`) — static analysis for Python and Actions code

All three checks (`test`, `analyze (python)`, `analyze (actions)`) are required before a PR can merge into `main`.
