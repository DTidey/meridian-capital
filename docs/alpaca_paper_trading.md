# Running on Alpaca Paper Trading

Alpaca is the book of record: positions and NAV are read from the account every night, and
orders are DAY limits placed by the nightly run after the US close, which Alpaca queues for
the next session. The following night's run records what filled.

## Prerequisites

- Alpaca credentials set in `.env`:
  ```
  ALPACA_API_KEY=<your-key>
  ALPACA_SECRET_KEY=<your-secret>
  ```
- `ALPACA_PAPER` is not required: the system uses the paper endpoint unless it is set to
  `false` (see [Switching to live trading](#switching-to-live-trading)).
- PostgreSQL reachable at `DATABASE_URL` in `.env`.
- NAV is the Alpaca account equity, recorded by each book sync. `portfolio.nav_usd` in
  `config.yaml` ($100,000) is used only before the first sync has recorded a NAV.

---

## How a trading day works

| When (UK) | What happens |
|---|---|
| Night N, after the US close | `run_all.py`: data, scores, **book sync**, rebalance, risk checks, orders submitted |
| Day N+1, US session | Alpaca works the queued DAY limit orders; anything unfilled expires at the close |
| Night N+1 | Order sync records fills and final statuses; book sync records the new positions and NAV |

The book sync (`run_execution.py --sync`) runs after Layer 3 and before Layer 4, including in
`--whatif` runs. It rebuilds `portfolio_positions` from Alpaca (signed shares, shorts
negative), writes that date's whole-book `portfolio_history` snapshot and records
`portfolio_nav`. In a real run a failed sync stops the pipeline before Layer 4, so the fund
never rebalances against a stale book.

Run on the UK evening of the US session date. Limit prices are the stored `close` for that
date +/- 0.5%. During US hours that close is not stored yet, and after UK midnight the default
`--date` has moved on: either way every approval is skipped (`no_close`) and execution exits 1.

---

## Step 1 — Preview (no commits)

```bash
cd ls_equity_fund
python run_all.py --whatif
```

Runs every layer and the book sync but commits nothing in Layers 4-5 and submits no orders
(Layer 6 only prints `--status`). Use it to check the proposed trades in the Layer 4 and
Layer 5 output. This is what the cron runs today.

---

## Step 2 — Rebalance with a dry run of the orders (optional)

To review before anything is submitted:

```bash
python run_all.py --dry-run
```

This is a real rebalance: Layer 4 writes PENDING approvals to `position_approvals` and Layer 5
stamps them APPROVED or REJECTED. Layer 6 then runs `--dry-run`, which:

- connects to Alpaca, reads the account and positions, and checks shortability for SHORT
  orders (Alpaca is asked only on a 7-day cache miss);
- runs the order sync, which does write any fills and statuses since the last run to
  `execution_orders`;
- runs the book sync without writing;
- logs each order it would place (`[DRY-RUN] ...`) and inserts an `execution_orders` row with
  status `DRY_RUN`; no orders are placed.

Inspect the approvals for the date in `position_approvals` (`status`, `action`,
`delta_shares`) and the dry-run order log. If anything looks wrong, adjust and re-run rather
than executing. Orders are not split by ADV: the
`max_adv_pct` setting is not applied (see `docs/backlog.md`).

---

## Step 3 — Submit the orders

Either run the whole pipeline:

```bash
python run_all.py
```

or, after Step 2 has created the approvals, submit them the same evening:

```bash
python run_execution.py --execute
```

`--execute` syncs orders and the book, then for each APPROVED row of the date submits a DAY
limit order (one order per approval) and records Alpaca's status, normally `PENDING`. It does
not wait for fills. Approvals are skipped with a warning when:

| Reason | Meaning |
|---|---|
| `no_close` | no stored close for the date |
| `open_order` | the ticker already has an open order at Alpaca |
| `no_alpaca_position` | a SELL or COVER for a position Alpaca does not hold |
| `not_shortable` | Alpaca reports the ticker not shortable or not easy to borrow |

If Alpaca's open orders cannot be listed, nothing is submitted. A book sync after submission
records positions and NAV (unchanged until the orders fill).

**Ctrl-C** while orders are being submitted cancels every open Alpaca order and exits. To
withdraw queued orders afterwards, before the US open:

```bash
python run_execution.py --cancel-pending
```

The next order sync marks them `CANCELLED`.

---

## Step 4 — Check the results the next night

The next nightly run records the outcome automatically. To do it by hand, after the US close:

```bash
python run_execution.py --sync        # order sync, then book sync and NAV
python run_execution.py --status      # orders still open (PENDING / PARTIAL)
python run_execution.py --slippage    # 30-day slippage over FILLED orders
```

Order statuses follow Alpaca: `FILLED`; `PARTIAL` (some fill, still working); `CANCELLED`
(cancelled or expired, `filled_shares` keeps any partial fill); `FAILED` (rejected). Fill
prices are in `execution_orders` and on the dashboard's Execution page. Slippage is measured
against the order's own limit price, so it is rarely positive.

---

## Resetting

Because Alpaca is the book of record, clearing the database alone does not start afresh: the
next book sync restores positions and NAV from the account. For a fresh start:

1. Reset the paper account in the Alpaca dashboard (or close its positions and cancel its
   orders).
2. Clear the execution and portfolio state:

   ```python
   import os

   import sqlalchemy as sa
   engine = sa.create_engine(os.environ["DATABASE_URL"])  # from .env
   tables = [
       "position_approvals", "execution_orders", "portfolio_positions",
       "position_trades", "portfolio_history", "portfolio_nav", "pnl_attribution",
       "risk_events", "risk_log",
   ]
   with engine.begin() as conn:
       conn.execute(sa.text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY"))
   ```

   Clearing `portfolio_nav` also clears the circuit breakers' history: daily and weekly P&L
   and drawdown start again from the next recorded NAV.
3. Remove `cache/halt.lock` if present (written when the circuit breakers halt trading) and
   `cache/risk_state.json` (the last risk snapshot; rebuilt by the next risk check).
4. Run `python run_execution.py --sync` to record the reset account's positions and NAV.

Market data and factor cache tables are unaffected.

---

## Switching to live trading

Only the environment variable selects the endpoint. Set in `.env`:

```
ALPACA_PAPER=false
```

and use the live account's `ALPACA_API_KEY` and `ALPACA_SECRET_KEY`. `execution.alpaca_paper`
in `config.yaml` is not read by any code; changing it has no effect.
