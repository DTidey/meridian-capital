# Layer 6 — Execution (Alpaca)

**Status:** Complete  
**Depends on:** Layer 5 (`position_approvals` with status=APPROVED)  
**Entry point:** `run_execution.py`

---

## Purpose

Layer 6 reads APPROVED rows from `position_approvals`, maps them to Alpaca
orders, submits them, polls until terminal, and writes results back to the DB.
It also reconciles live broker positions against the portfolio model and
auto-corrects any discrepancies.

---

## Design Decisions

| Decision | Choice |
|---|---|
| Time in force | `day` (expires at market close; safer for live trading) |
| Partial fills | Keep — only retry on zero fill |
| Market closed | Warn and continue (don't block) |
| Reconciliation | Auto-correct discrepancies after logging a warning |

---

## Database — `execution/db.py`

New table `execution_orders` on `data.db.metadata`:

| Column | Type | Notes |
|---|---|---|
| id | Integer PK autoincrement | |
| rebalance_date | String | Matches position_approvals.rebalance_date |
| ticker | String | |
| action | String | BUY / SELL / SHORT / COVER |
| ordered_shares | Float | Requested qty |
| filled_shares | Float | Actual filled qty (0 on open) |
| avg_fill_price | Float | Null until filled |
| order_id | String | Alpaca order UUID |
| status | String | PENDING / PARTIAL / FILLED / CANCELLED / FAILED |
| slippage_bps | Float | (avg_fill − limit_price) / limit_price × 10000 |
| created_at | String | ISO datetime |
| updated_at | String | ISO datetime |

---

## Module Structure

```
execution/
  __init__.py
  db.py           # execution_orders table
  broker.py       # Alpaca client, position sync, market clock
  short_check.py  # Shortable / easy-to-borrow check (7-day cache)
  executor.py     # Submit, poll, update
  order_manager.py# Order state machine, SIGINT handler
  costs.py        # Slippage computation and 30-day stats
```

---

## `execution/broker.py`

### `get_client() -> TradingClient`
Reads `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` from env (`.env` file or
environment). Reads `ALPACA_PAPER` (default `true`) — when `true` connects to
paper endpoint.

### `get_account() -> dict`
Returns buying power, equity, day_trade_count.

### `market_is_open() -> bool`
Calls Alpaca clock endpoint. Logs a WARNING when closed but does not raise.

### `get_broker_positions() -> dict[str, float]`
Returns `{ticker: signed_qty}` — positive for long, negative for short.

### `reconcile_positions(conn, cache_dir) -> list[dict]`
1. Fetch broker positions via `get_broker_positions()`.
2. Read `portfolio_positions` from DB.
3. Diff: for each ticker where `|broker_qty − db_qty| > 0.5`:
   a. Log a WARNING with ticker, broker_qty, db_qty.
   b. **Auto-correct**: update `portfolio_positions` to match broker qty.
   c. Append a dict `{ticker, broker_qty, db_qty, action: "corrected"}` to
      result list.
4. Return the list of corrections (empty if clean).

---

## `execution/short_check.py`

### `is_shortable(ticker, client, cache_dir) -> bool`
- Cache: `cache/shortable/{ticker}.json` with `{"shortable": bool, "ts": epoch}`.
- TTL: 7 days.
- On cache miss: call `client.get_asset(ticker)` and check
  `asset.shortable and asset.easy_to_borrow`.
- Returns `False` on any API error (safe default).

---

## `execution/executor.py`

### `_limit_price(side, current_price) -> float`
- BUY / COVER: `current_price × 1.005` (0.5% above — ensures fill in normal
  conditions while capping slippage).
- SELL / SHORT: `current_price × 0.995`.

### `_chunk_orders(ticker, shares, adv) -> list[float]`
If `|shares| / adv > 0.02`, split into chunks of `0.02 × adv` to avoid market
impact. Returns list of chunk sizes (all positive; caller handles sign).

### `submit_order(client, ticker, action, shares, current_price, dry_run=False) -> str | None`
- Maps action → Alpaca `OrderSide` and `PositionIntent`.
- Uses `LimitOrderRequest` with `time_in_force=TimeInForce.DAY`.
- If `dry_run=True`: logs the would-be order, returns `None`.
- Returns Alpaca order UUID on success.

### `poll_order(client, order_id, timeout_s=120, interval_s=5) -> dict`
Polls until status in {filled, cancelled, expired, rejected} or timeout.
Returns `{status, filled_qty, avg_fill_price}`.

### `execute_approvals(conn, client, score_date, config, cache_dir, dry_run=False) -> list[dict]`
1. Read all `position_approvals` rows where `rebalance_date=score_date` and
   `status=APPROVED`.
2. Load `portfolio_positions` for current prices.
3. For each approval:
   a. Check `short_check.is_shortable` for SHORT actions; skip with WARNING
      if not shortable.
   b. Chunk if ADV available.
   c. For each chunk: `submit_order()` → insert `execution_orders` row →
      `poll_order()` → update row with fill data.
   d. On zero fill: log WARNING, mark CANCELLED.
   e. On partial fill: keep — do not retry.
   f. Update `portfolio_positions` to reflect filled quantity.
4. Return list of result dicts.

---

## `execution/order_manager.py`

### `cancel_open_orders(client) -> int`
Cancels all open Alpaca orders. Returns count cancelled.

### `OrderManager`
Context manager that registers a SIGINT handler. On SIGINT:
1. Calls `cancel_open_orders(client)`.
2. Logs "Execution interrupted — open orders cancelled."
3. Exits gracefully.

---

## `execution/costs.py`

### `compute_slippage(ordered_price, filled_price, side) -> float`
Returns signed slippage in bps. Positive = adverse (paid more / received less
than limit).

### `slippage_stats(conn, days=30) -> dict`
Reads `execution_orders` for the last `days` days (status=FILLED).
Returns `{mean_bps, p95_bps, worst_ticker, count}`.

---

## `run_execution.py` — Entry Point

```
usage: run_execution.py [--dry-run] [--execute] [--status] [--sync]
                        [--slippage] [--cancel-pending] [--date DATE]
                        [--verbose]
```

| Flag | Action |
|---|---|
| `--dry-run` | Print orders that would be sent; no Alpaca calls |
| `--execute` | Submit orders for today's APPROVED position_approvals |
| `--status` | Print open execution_orders rows |
| `--sync` | Run reconcile_positions and print corrections |
| `--slippage` | Print 30-day slippage stats |
| `--cancel-pending` | Cancel all open Alpaca orders |
| `--date DATE` | Use DATE instead of today |
| `--verbose` | INFO logging |

Flow for `--execute`:
1. `reconcile_positions()` — auto-correct any drift.
2. `market_is_open()` — warn if closed, continue.
3. `execute_approvals()` inside `OrderManager` context.
4. Print summary table: ticker | action | ordered | filled | avg_price | slippage_bps.

---

## Configuration (`config.yaml` additions)

```yaml
execution:
  alpaca_paper: true          # false → live trading
  limit_slippage_pct: 0.005   # 0.5% limit buffer
  max_adv_pct: 0.02           # chunk threshold
  poll_timeout_s: 120
  poll_interval_s: 5
  shortable_cache_days: 7
```

---

## Environment Variables (`.env.example`)

```
ALPACA_API_KEY=your_key
ALPACA_SECRET_KEY=your_secret
ALPACA_PAPER=true
```

---

## Tests (`tests/test_execution_*.py`)

All tests use a mock Alpaca client (no live API calls).

| File | Covers |
|---|---|
| `test_execution_db.py` | Table creation, insert/query execution_orders |
| `test_execution_broker.py` | reconcile_positions auto-correct, market_is_open warning |
| `test_execution_short_check.py` | Cache hit/miss, not-shortable → False |
| `test_execution_executor.py` | limit_price, chunk_orders, submit_order dry-run, partial fill kept |
| `test_execution_costs.py` | compute_slippage bps, slippage_stats aggregation |
| `test_execution_order_manager.py` | cancel_open_orders count, SIGINT handler |

Total: ~30 tests.

---

## Layer 7 Interface

`run_execution.py` leaves data for Layer 7 in:
- `execution_orders` — trade-level fills, slippage
- `portfolio_positions` — updated share counts
- `portfolio_history` — unchanged (Layer 7 snapshots from portfolio_positions)
