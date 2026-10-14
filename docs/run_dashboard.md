# Running the JARVIS Dashboard

The front end is a Streamlit app at `ls_equity_fund/dashboard/app.py`. It reads from PostgreSQL and
shows the fund across seven pages.

## Prerequisites

1. **Database reachable.** `config.yaml` (`database.url`) points at the fund's PostgreSQL server;
   set `DATABASE_URL` in `.env` to use another one, e.g. a local container started with:

   ```bash
   docker compose up -d
   ```

2. **Environment variables.** A `.env` file must exist at `ls_equity_fund/.env`:

   ```bash
   cp .env.example .env   # then fill in the keys
   ```

   The LLM key (`OPENAI_API_KEY` or `OPENROUTER_API_KEY`) is needed for JARVIS, the LP letter and
   filing summaries; the rest of the dashboard works without it.

3. **Python environment** (from `ls_equity_fund/`):

   ```bash
   make venv && make sync
   ```

## Starting the dashboard

```bash
cd ls_equity_fund
. .venv/bin/activate && streamlit run dashboard/app.py --server.port 8502
```

Then open [http://localhost:8502](http://localhost:8502). Restart it after pulling new code; it
initialises the database schema (adding any new columns) when it starts.

## Pages

| # | Page | Contents |
|---|------|----------|
| I | Portfolio | Current positions, weights, system metrics, and the Ask JARVIS chat |
| II | Research | Candidate cards with AI analysis, factor heatmap, and the pending trade approvals |
| III | Risk | Circuit breakers, tail risk, risk decomposition, factor exposures, stress tests, correlation heatmap, recent alerts |
| IV | Performance | NAV series, P&L attribution, win/loss, JARVIS weekly commentary |
| V | Execution | Order blotter, fills, transaction costs |
| VI | Letter | Auto-generated LP letter draft |
| VII | Ticker | Any S&P 500 ticker: price, valuation, signal, factor and AI scores, fundamentals, estimates, short interest, insider activity, institutional holdings, and SEC filings with a reader and AI summaries |

## What the dashboard writes or calls

The dashboard is not read-only. In particular, **it can place orders**:

- **Research:** "Execute All Approved Trades" runs the pre-trade veto checks on pending trades,
  marks those that pass APPROVED in `position_approvals`, and then runs `run_execution.py`,
  which submits orders to Alpaca. With `ALPACA_PAPER=false` these are live orders.
- **Portfolio:** Ask JARVIS calls the LLM (not saved).
- **Letter:** opening the page generates today's LP letter (an LLM call, saved to `lp_letters`)
  if it hasn't been generated yet; "Regenerate" makes a new one.
- **Performance:** "Regenerate Commentary" calls the LLM and saves new weekly commentary.
- **Ticker:** opening a filing fetches it from SEC EDGAR (cached for 24 hours); "Generate
  summary" calls the LLM (`analysis.openai_model_cheap`) and saves the summary with its cost.

## Auto-refresh

During market hours (9:30–16:00 ET, weekdays) the dashboard refreshes automatically every
5 minutes. Outside market hours it is static until reloaded.

## Notes

- To populate data, run the pipeline first: see [run_all.md](run_all.md).
- The LLM provider comes from `llm.provider` in `config.yaml` (`LLM_PROVIDER` in `.env` overrides
  it). Models: per-analyzer models in `analysis.analyzer_models` (earnings and risk on gpt-4o, filing and insider on gpt-4o-mini by default); `analysis.openai_model` for JARVIS, the LP letter and commentary; `analysis.openai_model_cheap` for filing summaries.
