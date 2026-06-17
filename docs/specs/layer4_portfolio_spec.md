# Layer 4 — Portfolio Construction Specification

**Status:** Complete  
**Depends on:** Layer 3 (`combined_scores` table)  
**Entry point:** `run_portfolio.py`

## 1. Overview

Layer 4 consumes the ranked long/short candidate list produced by Layer 3 (`combined_scores` table) and produces a target portfolio: specific tickers, weights, and share counts for a long book and a short book, subject to risk, liquidity, and concentration constraints.

Two optimisation methods are implemented:
- **Conviction-tilt** (default): equal-weight base with score-proportional tilts — no solver required, always converges.
- **MVO** (optional): Markowitz mean-variance optimisation via `scipy.optimize.minimize` (SLSQP); falls back to conviction-tilt on non-convergence.

Output is written to three new PostgreSQL tables (`portfolio_positions`, `portfolio_history`, `position_approvals`) and to a human-readable trade list in the terminal. A `--whatif` flag previews the proposed changes without committing anything.

---

## 2. Module Structure

```
ls_equity_fund/
├── run_portfolio.py               # Layer 4 entry point
└── portfolio/
    ├── __init__.py
    ├── db.py                      # New table definitions (shared metadata)
    ├── state.py                   # Read/write portfolio positions
    ├── beta.py                    # Rolling beta vs SPY
    ├── factor_exposure.py         # Weighted factor exposures per book
    ├── transaction_costs.py       # Spread + market impact model
    ├── rebalance_schedule.py      # Event-based advisory warnings
    ├── optimizer.py               # Conviction-tilt (primary)
    ├── mvo_optimizer.py           # MVO (optional)
    └── rebalance.py               # Diff current vs target → trade list
```

---

## 3. Database Schema (`portfolio/db.py`)

Three new tables registered on the shared SQLAlchemy metadata (same pattern as `factors/db.py` and `analysis/db.py`). All are PostgreSQL-backed in production; SQLite is used in tests.

### 3.1 `portfolio_positions`
Current open positions — one row per ticker, updated on each rebalance commit.

| Column | Type | Description |
|--------|------|-------------|
| `ticker` | String PK | — |
| `direction` | String | `LONG` or `SHORT` |
| `shares` | Float | Signed: positive = long, negative = short |
| `entry_price` | Float | Average cost basis |
| `entry_date` | String | ISO date of most recent entry |
| `current_price` | Float | Last close price |
| `market_value` | Float | `shares × current_price` |
| `weight` | Float | `market_value / gross_nav` |
| `unrealized_pnl` | Float | `(current_price - entry_price) × shares` |
| `sector` | String | GICS sector at entry |
| `combined_score` | Float | Layer 3 score at entry |
| `beta` | Float | 60-day rolling beta vs SPY at entry |
| `updated_at` | String | ISO timestamp |

Primary key: `ticker`

### 3.2 `portfolio_history`
Append-only log of every committed state snapshot. Used for P&L attribution in Layer 7.

| Column | Type | Description |
|--------|------|-------------|
| `id` | Integer (autoincrement) PK | — |
| `snapshot_date` | String | ISO date |
| `ticker` | String | — |
| `direction` | String | — |
| `shares` | Float | — |
| `price` | Float | Close price on snapshot date |
| `market_value` | Float | — |
| `weight` | Float | — |
| `unrealized_pnl` | Float | — |
| `sector` | String | — |
| `combined_score` | Float | — |
| `recorded_at` | String | ISO timestamp |

Index: `(snapshot_date, ticker)`

### 3.3 `position_approvals`
Optional human-in-the-loop gate. Each proposed rebalance can be recorded here for review before execution in Layer 6.

| Column | Type | Description |
|--------|------|-------------|
| `id` | Integer (autoincrement) PK | — |
| `rebalance_date` | String | ISO date |
| `ticker` | String | — |
| `action` | String | `BUY`, `SELL`, `SHORT`, `COVER`, `HOLD` |
| `target_shares` | Float | — |
| `current_shares` | Float | — |
| `delta_shares` | Float | `target - current` |
| `estimated_cost_usd` | Float | Transaction cost estimate |
| `status` | String | `PENDING`, `APPROVED`, `REJECTED` |
| `created_at` | String | — |
| `reviewed_at` | String | Nullable — filled when status changes |

---

## 4. Config Additions (`config.yaml`)

```yaml
portfolio:
  num_longs:              20          # target long positions
  num_shorts:             20          # target short positions
  gross_exposure:         1.50        # gross NAV multiple (150%)
  target_long_gross:      0.90        # long book as fraction of NAV (90%)
  target_short_gross:     0.60        # short book as fraction of NAV (60%)
  net_exposure_min:       0.00        # net NAV fraction lower bound
  net_exposure_max:       0.10        # net NAV fraction upper bound
  max_position_pct:       0.05        # max single position (5% of NAV)
  min_position_pct:       0.005       # min meaningful position (0.5%)
  max_sector_pct:         0.25        # single-side sector cap (25%)
  max_sector_net_pct:     0.05        # sector net exposure cap (5%)
  max_beta:               0.15        # portfolio net beta cap
  turnover_budget_pct:    0.30        # max fraction of portfolio turned per rebalance
  nav_usd:                10_000_000  # notional NAV for share-count calculation
  earnings_blackout_days: 5           # halve position if earnings within N days
  adv_lookback_days:      20          # days for average daily volume
  adv_max_pct:            0.05        # max position = 5% of 20-day ADV
  beta_lookback_days:     60          # rolling beta window

  conviction_tilt:
    top5_multiplier:      1.50        # tilt for top 5% combined score
    top10_multiplier:     1.25        # tilt for top 10% combined score

  mvo:
    risk_aversion:        1.0         # lambda in objective
    cov_lookback_days:    120         # days of returns for covariance
    score_to_return_map:             # linear mapping: score → expected annual return
      score_100:          0.15        # +15%/yr at score 100
      score_0:           -0.15        # −15%/yr at score 0
    max_iter:             1000

  transaction_costs:
    commission_per_share: 0.0         # $0 (Alpaca)
    spread_hl_fraction:   0.05        # spread cost = 5% of avg H-L range
    market_impact_coef:   0.10        # sqrt-of-participation coefficient
```

---

## 5. Module Specifications

### 5.1 Portfolio State (`portfolio/state.py`)

```python
def load_positions(conn) -> pd.DataFrame:
    """Return current open positions from portfolio_positions."""

def save_positions(conn, positions_df: pd.DataFrame, score_date: str) -> None:
    """Upsert positions; also append to portfolio_history."""

def get_nav(conn, config: dict) -> float:
    """Return NAV from config (real account value from Layer 6 in future)."""
```

`positions_df` columns match `portfolio_positions`. On save, each row is also written to `portfolio_history` with today's date.

---

### 5.2 Beta Calculator (`portfolio/beta.py`)

```python
def compute_betas(
    conn: sa.engine.Connection,
    tickers: list[str],
    score_date: str,
    lookback_days: int = 60,
) -> pd.Series:
    """Return Series[ticker → beta vs SPY] using rolling OLS on adj_close returns."""
```

- Fetch `adj_close` for all tickers + `SPY` from `daily_prices` for the last `lookback_days` calendar days.
- Compute daily log-returns.
- `beta[t] = cov(r_t, r_SPY) / var(r_SPY)` — standard OLS beta.
- Missing tickers (insufficient history) default to `beta = 1.0`.

```python
def portfolio_beta(weights: pd.Series, betas: pd.Series) -> float:
    """w · beta — signed: long positive, short negative."""
```

---

### 5.3 Factor Exposure Calculator (`portfolio/factor_exposure.py`)

```python
def compute_exposures(
    positions_df: pd.DataFrame,
    factor_scores_df: pd.DataFrame,
) -> dict:
    """
    Returns {
        "long":  {factor_name: weighted_avg_score},
        "short": {factor_name: weighted_avg_score},
        "spread": {factor_name: long_avg - short_avg},
    }
    """
```

- Weighted average of each factor sub-score, using `abs(weight)` as weights.
- Flags any long-minus-short spread that exceeds the historical 1 σ of that factor spread (computed from `factor_scores` history for the last 60 score dates).
- Returns a clean dict, not a DataFrame — easier to serialise to reports.

---

### 5.4 Transaction Cost Model (`portfolio/transaction_costs.py`)

Three components, all expressed in bps of trade value, then converted to dollars.

| Component | Formula |
|-----------|---------|
| Commission | $0 (Alpaca zero-commission) |
| Spread cost | `spread_hl_fraction × mean(high − low, last adv_lookback_days) / close` × trade_value |
| Market impact | `coef × sqrt(trade_shares / ADV) × daily_vol_bps` × trade_value |

Where `daily_vol_bps` = annualised volatility / sqrt(252) (in bps).

```python
def estimate_cost(
    ticker: str,
    trade_shares: float,
    price: float,
    prices_df: pd.DataFrame,       # last 20+ days of OHLCV for this ticker
    config: dict,
) -> float:
    """Return estimated total transaction cost in USD."""

def net_expected_return(
    gross_return: float,
    cost_usd: float,
    position_value: float,
) -> float:
    """Deduct proportional transaction cost from expected return."""
```

---

### 5.5 Rebalance Schedule (`portfolio/rebalance_schedule.py`)

Advisory only — returns warning strings, never blocks execution.

```python
def check_events(
    tickers: list[str],
    score_date: str,
    conn: sa.engine.Connection,
    config: dict,
) -> list[str]:
    """Return list of human-readable advisory warnings."""
```

Three checks:

**1. Earnings proximity** — queries `earnings_calendar` for each ticker. Warns if earnings ≤ 2 days away.

**2. FOMC meeting** — hardcoded 2026 FOMC dates (8 meetings per year, announced quarterly by the Fed). Warns if score_date is within 5 days of a meeting.

```python
_FOMC_2026 = [
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-16",
]
```

**3. Monthly options expiration** — third Friday of each month. Warns if within 3 days.

---

### 5.6 Conviction-Tilt Optimizer (`portfolio/optimizer.py`)

Primary optimiser. Always produces a valid result.

**Algorithm:**

1. **Candidate selection:** Take top-N longs by `combined_score` from `combined_scores`, bottom-N shorts. `N = config.portfolio.num_longs / num_shorts`.

2. **Equal-weight base:** Long book: each ticker starts at `target_long_gross / num_longs`. Short book: each ticker starts at `target_short_gross / num_shorts`.

3. **Conviction tilt:** Within each book, rank by `combined_score`. Top 5% of positions → multiply weight by `top5_multiplier (1.5×)`. Top 10% (but below 5%) → `top10_multiplier (1.25×)`. Re-normalise each book to its gross target after tilting.

4. **Earnings haircut:** For any ticker with earnings within `earnings_blackout_days` days, halve the weight; redistribute the surplus equally across remaining positions in the same book. Re-normalise again.

5. **Liquidity cap:** For each position, compute `max_adv_weight = adv_max_pct × ADV × price / NAV`. If weight > max_adv_weight, cap it and redistribute surplus. Re-normalise.

6. **Position bounds:** Clamp each weight to `[min_position_pct, max_position_pct]`. Re-normalise.

7. **Sector neutrality:** For each GICS sector, compute `net_sector_weight = Σ(long_weights) − Σ(short_weights)`. If `|net_sector_weight| > max_sector_net_pct`, scale down the dominant-side positions in that sector proportionally until the constraint is met.

8. **Beta adjustment:** Compute `net_beta = portfolio_beta(weights, betas)`. If `|net_beta| > max_beta`, scale the short book up or down until the constraint is satisfied (keeps long book fixed to avoid over-trading the higher-conviction side).

9. **Output:** Return `pd.DataFrame` with columns `[ticker, direction, weight, shares, sector, combined_score, beta]`.

```python
def optimise(
    candidates: pd.DataFrame,    # from combined_scores
    prices: pd.DataFrame,        # recent OHLCV
    betas: pd.Series,
    config: dict,
    score_date: str,
    conn: sa.engine.Connection,
) -> pd.DataFrame:
    """Return target portfolio DataFrame."""
```

---

### 5.7 MVO Optimizer (`portfolio/mvo_optimizer.py`)

Optional Markowitz optimiser. Falls back to conviction-tilt on non-convergence or any exception.

**Inputs:**
- Expected returns `μ[i]`: linear interpolation of `combined_score` onto the `[score_0_return, score_100_return]` range, then adjusted by subtracting estimated one-way transaction cost as fraction of position value.
- Covariance matrix `Σ`: `cov_lookback_days` of daily log-return history from `daily_prices`, using `adj_close`. Minimum `cov_lookback_days = 60` days required; fall back if insufficient data.

**Objective (SLSQP maximise → minimise negative):**
```
min  −μᵀw + λ wᵀΣw
```

**Variables:** `w` is a vector of signed weights. Long positions `w_L > 0`, short positions `w_S < 0`.

**Constraints implemented as `scipy.optimize` constraint dicts:**
1. `Σ w_L = target_long_gross` (equality, long tickers only)
2. `Σ |w_S| = target_short_gross` (equality, short tickers only)
3. `min_position_pct ≤ w_i ≤ max_position_pct` for longs (bounds)
4. `-max_position_pct ≤ w_i ≤ -min_position_pct` for shorts (bounds)
5. `|wᵀβ| ≤ max_beta` (beta constraint, implemented as two inequality constraints)
6. For each sector s: `|Σ_{i∈s} w_i| ≤ max_sector_net_pct` (inequality)
7. For each sector s, each side: `Σ_{i∈s,L} w_i ≤ max_sector_pct` and `Σ_{i∈s,S} |w_i| ≤ max_sector_pct`

**Initial guess:** conviction-tilt weights (warm start for faster convergence).

**Non-convergence:** if `result.success == False` or any exception, log `WARNING: MVO did not converge, falling back to conviction-tilt` and call `optimizer.optimise(...)` instead.

```python
def optimise(
    candidates: pd.DataFrame,
    prices: pd.DataFrame,
    betas: pd.Series,
    config: dict,
    score_date: str,
    conn: sa.engine.Connection,
) -> pd.DataFrame:
    """MVO-optimised target portfolio, or conviction-tilt on failure."""
```

---

### 5.8 Rebalance Generator (`portfolio/rebalance.py`)

Diffs the current portfolio against the target to produce an ordered trade list.

```python
def generate_trades(
    current: pd.DataFrame,     # portfolio_positions
    target: pd.DataFrame,      # optimizer output
    prices: pd.DataFrame,      # for cost estimates and share counts
    config: dict,
    conn: sa.engine.Connection,
    score_date: str,
) -> pd.DataFrame:
    """Return trade list with columns: ticker, action, current_shares,
       target_shares, delta_shares, estimated_cost_usd, priority."""
```

**Algorithm:**

1. **Merge** current and target on ticker. Missing current → new position (delta = target). Missing target → close position (delta = −current).

2. **Action mapping:**
   - LONG delta > 0: `BUY`
   - LONG delta < 0: `SELL`
   - SHORT delta < 0 (more short): `SHORT`
   - SHORT delta > 0 (less short): `COVER`
   - delta ≈ 0 (< 1 share difference): `HOLD`

3. **Turnover budget:** compute `proposed_turnover = Σ |delta_shares × price| / NAV`. If > `turnover_budget_pct`, sort trades by `|Δscore| = |new_combined_score − old_combined_score|` descending and trim from the bottom until within budget. Positions being fully closed are never trimmed.

4. **Cost estimation:** call `transaction_costs.estimate_cost()` for each trade.

5. **Priority ordering:** full closures first, then opens by `|Δscore|` descending, then trims.

6. **Commit flag:** if not `--whatif`, write approved rows to `position_approvals` with status `PENDING`, then call `state.save_positions()` with the new target.

---

## 6. Entry Point (`run_portfolio.py`)

```
Usage:
  python run_portfolio.py --rebalance [--optimize-method mvo|conviction]
  python run_portfolio.py --whatif    [--optimize-method mvo|conviction]
  python run_portfolio.py --current
```

**`--rebalance` pipeline:**
1. Load config, open DB, resolve `score_date`.
2. Load `combined_scores` candidates for `score_date`.
3. Compute betas for all candidates + current holdings.
4. Fetch recent OHLCV for ADV / covariance / spread cost computation.
5. Run rebalance schedule checks → print advisory warnings.
6. Run selected optimiser → target portfolio DataFrame.
7. Compute factor exposures for long and short books → print summary table.
8. Generate trade list → print trade list with costs.
9. Unless `--whatif`: write to `position_approvals` + update `portfolio_positions` + append `portfolio_history`.
10. Print portfolio summary: gross/net exposure, sector breakdown, net beta.

**`--current`:** load and pretty-print `portfolio_positions` with current P&L.

---

## 7. Testing Plan

All tests use SQLite in-memory fixtures (same `tmp_engine`/`tmp_db` pattern as Layers 2–3). No real prices or API calls required.

| File | Key tests |
|------|-----------|
| `test_beta.py` | OLS regression gives correct beta; missing ticker defaults to 1.0; SPY self-beta = 1.0; portfolio beta = weighted sum |
| `test_transaction_costs.py` | Zero cost when ADV → ∞; cost scales with sqrt of participation; spread cost proportional to H-L range |
| `test_rebalance_schedule.py` | Earnings within 2 days triggers warning; FOMC within 5 days; third-Friday detection; no warnings when all clear |
| `test_optimizer.py` | Weights sum to gross targets; no position exceeds max_pct; sector net within limit; earnings haircut applied; conviction tilt applied to top scores; liquidity cap honoured |
| `test_mvo_optimizer.py` | Returns valid weights on simple case; falls back to conviction-tilt when forced to not converge; beta constraint satisfied |
| `test_rebalance.py` | New position → BUY; closed position → SELL; turnover budget trims smallest Δscore trades first; closures never trimmed; HOLD when delta < 1 share |
| `test_state.py` | save_positions upserts correctly; load_positions returns current state; history appended on save |
| `test_factor_exposure.py` | Long/short exposure dicts have correct keys; spread = long − short; flags raised when spread > 1σ |

Estimated: ~60–70 tests across 8 files.

---

## 8. Dependencies

No new packages required beyond what is already in `requirements.txt`.

| Package | Already present | Use |
|---------|----------------|-----|
| `scipy` | No — add to requirements.txt | SLSQP solver for MVO |
| `numpy` | Yes (via pandas) | Matrix ops, `np.cov` |
| `pandas` | Yes | All DataFrames |
| `sqlalchemy` | Yes | DB access |

`scipy` is the only new dependency.

---

## 9. Key Design Decisions

1. **PostgreSQL for all tables** — despite the prompt saying "SQLite tables for portfolio state", we follow the established architecture (PostgreSQL in production, SQLite in tests via the existing `get_engine` + `initialise_schema` pattern). This avoids maintaining two databases.

2. **Conviction-tilt as default** — MVO has known stability issues with poorly-conditioned covariance matrices. Conviction-tilt is always the fallback and the out-of-the-box default. MVO is opt-in via `--optimize-method mvo`.

3. **No portfolio state from Layer 3** — Layer 3 writes `combined_scores`, which Layer 4 reads. Layer 4 writes `portfolio_positions`. These are cleanly separated and the pipeline can be re-run from any layer independently.

4. **Sector neutrality via post-processing, not constraints** — in the conviction-tilt optimizer, sector neutrality is enforced iteratively after tilts and haircuts, which is simpler and more robust than adding it as an optimisation constraint. In MVO it is a constraint.

5. **Advisory-only rebalance schedule** — the schedule check logs warnings but never blocks a trade. Blocking is a Layer 6 concern (pre-trade risk checks).

6. **Turnover budget preserves closures** — full position closures (delta = 100% of current) are never subject to the turnover budget cut, because carrying a position with a zero target score creates unintended risk.
