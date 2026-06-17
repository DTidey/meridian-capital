# Meridian Capital Partners — Layer 6: Execution (Alpaca)

Build Layer 6 of the Meridian Capital Partners hedge fund. Layers 1–5 are built.  
Build the **Alpaca paper trading execution layer.**

---

## Components

### 1. Broker Connection (`execution/broker.py`)

- Alpaca API using keys from `.env`: `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`
- **DEFAULT: paper trading** — hardcode paper base URL
- Live trading requires: `mode: live` in config **AND** typing `"YES I UNDERSTAND THE RISKS"`
- Sync portfolio state with Alpaca on startup
- Exponential backoff on failures

---

### 2. Order Executor (`execution/executor.py`)

Per trade, execute in order:

| Step | Action |
|------|--------|
| a | Pre-trade veto check |
| b | Short availability check |
| c | Limit price: `close * (1 +/- 0.001)` |
| d | Chunk orders > 2% ADV |
| e | 120s time-in-force |
| f | Poll every 5s |
| g | Cancel + retry on timeout (3x max) |
| h | Record `signal_price` for slippage calculation |

Log every order: `timestamp`, `ticker`, `side`, `shares`, `limit`, `fill`, `slippage_bps`, `status`

---

### 3. Slippage Tracker (`execution/costs.py`)

```
slippage = (fill - signal) / signal * 10,000 bps
```

- 30-day rolling stats: avg, median, p95, total dollar cost
- Surface worst 5 fills for dashboard

---

### 4. Short Availability (`execution/short_check.py`)

- Check Alpaca `"shortable"` + `"easy_to_borrow"` flags
- Cache 7 days
- Log and skip if not available

---

### 5. Order Manager (`execution/order_manager.py`)

- Track order states: pending / partial / filled / cancelled
- `SIGINT` → cancel pending orders, keep positions, log

---

## Entry Point

**`run_execution.py`**

| Flag | Description |
|------|-------------|
| `--dry-run` | Log what would happen without placing orders |
| `--execute` | Place live/paper orders |
