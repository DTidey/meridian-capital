# Meridian Capital Partners — Layer 4: Portfolio Construction

Build Layer 4 of the Meridian Capital Partners hedge fund. Layers 1–3 are built.  
Build portfolio construction with **two optimization methods: MVO and conviction-tilt.**

---

## Components

### 1. MVO Optimizer (`portfolio/mvo_optimizer.py`)

Markowitz optimization via `scipy.optimize.minimize` (SLSQP).

**Inputs:**
- Expected returns: composite score mapped linearly — score 100 = +15%/yr, score 0 = −15%/yr
- Covariance matrix: 120-day historical (replaced by factor-cov from Layer 5 later)
- Risk aversion lambda (default 1.0)
- Transaction costs per ticker subtracted from gross expected return

**Objective:**
```
maximize  mu*w - lambda*w*Sigma*w
```

**Constraints:**

| Constraint | Rule |
|------------|------|
| Long weights | Sum to `target_long_gross` |
| Short weights | Sum to `target_short_gross` |
| Per-position | `[min_pct, max_pct]` |
| Beta exposure | `\|w*beta\| <= 0.15` |
| Sector net | `\|sector_net\| <= 5%` |
| Single-side sector | `<= 25%` |

- On non-convergence: log warning, use conviction-tilt as fallback
- CLI flag: `--optimize-method mvo` or `--optimize-method conviction`

---

### 2. Conviction-Tilt Optimizer (`portfolio/optimizer.py`)

- Equal weight base within each book
- Top 5% scores get **1.5x**, top 10% get **1.25x**
- Liquidity: no position > 5% of 20-day ADV
- Earnings: halve size if earnings within 5 days
- Beta adjustment: scale so beta-adjusted exposure matches beta 1.0
- Sector neutral

---

### 3. Transaction Cost Model (`portfolio/transaction_costs.py`)

Three components per ticker (in bps):

| Component | Calculation |
|-----------|-------------|
| Commission | $0 (Alpaca) |
| Spread cost | 5% of avg daily H-L range |
| Market impact | `coef * sqrt(trade_size/ADV) * daily_vol_bps`, coef = 0.10 |

Fed into MVO objective so optimizer sees net-of-cost expected returns.

---

### 4. Rebalance Schedule (`portfolio/rebalance_schedule.py`)

Check events and return advisory warnings — does not block trading:

| Event | Threshold |
|-------|-----------|
| Positions with earnings | Within 2 days |
| FOMC meeting | Within 5 days (hardcode 2026 dates) |
| Monthly options expiration | Within 3 days (third Friday) |

---

### 5. Portfolio State (`portfolio/state.py`)

SQLite tables: `portfolio_positions`, `portfolio_history`, `position_approvals`

Track per position: `ticker`, `shares`, `entry_price`, `entry_date`, `current_price`, `unrealized_pnl`, `sector`, `factor_scores_at_entry`

- Handle corporate actions

---

### 6. Beta Calculator (`portfolio/beta.py`)

- Rolling 60-day beta per stock vs SPY
- Portfolio-level: long book beta, short book beta, net portfolio beta

---

### 7. Factor Exposure Calculator (`portfolio/factor_exposure.py`)

- Weighted average of each factor score across long and short book
- Flag if any spread exceeds 1 std dev from historical

---

### 8. Rebalance Generator (`portfolio/rebalance.py`)

- Compare current to target, generate trade list
- Apply turnover budget (max 30%)
- Prioritize largest score changes
- Estimate transaction costs per trade
- Include `--whatif` mode (show proposed changes without committing)

---

## Entry Point

**`run_portfolio.py`**

| Flag | Description |
|------|-------------|
| `--rebalance` | Run full rebalance |
| `--whatif` | Preview changes without committing |
| `--current` | Show current portfolio state |
| `--optimize-method mvo` | Use MVO optimizer |

**Config defaults:**

| Parameter | Value |
|-----------|-------|
| `num_longs` | 20 |
| `num_shorts` | 20 |
| `max_position` | 5% |
| `max_sector` | 25% |
| `gross` | 150% |
| `net` | [0%, +10%] |
| `max_beta` | 0.15 |
| `turnover_budget` | 30% |
| `mvo_risk_aversion` | 1.0 |
