# Running on Alpaca Paper Trading

## Prerequisites

- Alpaca credentials set in `.env`:
  ```
  ALPACA_API_KEY=<your-key>
  ALPACA_SECRET_KEY=<your-secret>
  ```
- `ALPACA_PAPER` is not required — the system defaults to paper trading unless explicitly set to `false`
- Paper account equity should match `portfolio.nav_usd` in `config.yaml` (default: $100,000)
- PostgreSQL database running and tables cleared (see [Resetting the database](#resetting-the-database))

---

## Step 1 — Run the data and signal pipeline

Layers 1–5 do not touch Alpaca and can be run while the market is closed. The
`--whatif` flag runs everything through risk checks and writes to
`position_approvals`, but skips order submission.

```bash
cd ls_equity_fund
python run_all.py --whatif
```

---

## Step 2 — Review the proposed trades

Inspect the risk snapshot and pending approvals before market open:

```bash
python run_risk_check.py --whatif     # risk dashboard snapshot
python run_execution.py --status      # pending position_approvals
```

Check that directions, sizes, and sector mix look sensible. If anything looks
wrong, re-run scoring or adjust config before proceeding.

---

## Step 3 — Dry-run the execution (optional)

Walks through the full order logic — shortability checks, ADV chunking, limit
price calculation — and logs to `execution_orders` with status `DRY_RUN`. No
Alpaca API calls are made.

```bash
python run_execution.py --dry-run
```

---

## Step 4 — Execute during market hours

Run the full pipeline (if starting fresh):

```bash
python run_all.py
```

Or, if Layers 1–5 have already run and approvals are in place:

```bash
python run_execution.py --execute
```

The system submits DAY limit orders with a 0.5% slippage buffer, polls for
fills every 5 seconds (timeout 120 seconds per order), and records results in
`execution_orders`. **Ctrl-C** triggers graceful shutdown and cancels any open
orders.

---

## Step 5 — Verify fills

```bash
python run_execution.py --status      # execution_orders with fill prices
python run_execution.py --slippage    # 30-day slippage statistics
python run_execution.py --sync        # reconcile DB positions against Alpaca
```

---

## Resetting the database

To clear all execution and portfolio state (e.g. before a fresh start):

```python
import sqlalchemy as sa
engine = sa.create_engine("postgresql+psycopg2://meridian:meridian@localhost:5432/meridian")
tables = [
    "position_approvals", "execution_orders", "position_trades",
    "portfolio_history", "portfolio_nav", "pnl_attribution",
    "risk_events", "risk_log",
]
with engine.begin() as conn:
    conn.execute(sa.text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY"))
```

Market data and factor cache tables are unaffected.

---

## Switching to live trading

Set in `.env`:

```
ALPACA_PAPER=false
```

Update `config.yaml`:

```yaml
execution:
  alpaca_paper: false
```

> **Note:** The environment variable takes precedence over `config.yaml`.
