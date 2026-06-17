# Running the JARVIS Dashboard

The front end is a Streamlit app located at `ls_equity_fund/dashboard/app.py`. It connects to PostgreSQL and displays live portfolio data across six pages: Portfolio, Research, Risk, Performance, Execution, and Letter.

## Prerequisites

1. **PostgreSQL running** — the database must be up before starting the dashboard:

   ```bash
   docker compose up -d
   ```

2. **Environment variables** — a `.env` file must exist at `ls_equity_fund/.env`. Copy the example if you haven't already:

   ```bash
   cp ls_equity_fund/.env.example ls_equity_fund/.env
   # then fill in API keys
   ```

3. **Python dependencies** installed (from `ls_equity_fund/`):

   ```bash
   pip install -r requirements.txt
   ```

## Starting the dashboard

Run from the `ls_equity_fund/` directory:

```bash
cd ls_equity_fund
streamlit run dashboard/app.py --server.port 8502
```

Then open [http://localhost:8502](http://localhost:8502) in your browser.

## Pages

| # | Page | Contents |
|---|------|----------|
| I | Portfolio | Current positions, weights, factor scores |
| II | Research | AI qualitative analysis, earnings, insider activity |
| III | Risk | Greeks, circuit breaker status, stress test results |
| IV | Performance | NAV series, P&L attribution, win/loss |
| V | Execution | Order blotter, fills, transaction costs |
| VI | Letter | Auto-generated LP letter draft |

## Auto-refresh

During market hours (9:30–16:00 ET, weekdays) the dashboard refreshes automatically every 5 minutes. Outside market hours it is static until manually reloaded.

## Notes

- The dashboard is read-only — it does not trigger any trades or pipeline runs.
- To populate data, run the pipeline first: see [run_all.md](run_all.md).
- The default database URL is `postgresql+psycopg2://meridian:meridian@localhost:5432/meridian`. Override it by setting `DATABASE_URL` in `.env`.
