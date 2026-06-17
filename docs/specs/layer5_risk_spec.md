# Layer 5 — Risk Management Specification
# Meridian Capital Partners

**Status:** Complete  
**Depends on:** Layers 1–4 complete and working  
**Entry point:** `run_risk_check.py`

---

## 1. Overview and Design Philosophy

Layer 5 is a post-optimisation, pre-execution risk gate. It sits between Layer 4
(which writes PENDING trades to `position_approvals`) and Layer 6 (which executes
APPROVED trades via Alpaca). Layer 5's outputs are:

1. APPROVED or REJECTED stamps on each PENDING trade row in `position_approvals`
2. A circuit-breaker sizing adjustment (SIZE_DOWN) or halt lock (`cache/halt.lock`)
3. A persistent `cache/risk_state.json` with daily risk metrics and alerts
4. Stress test P&L reports (on demand)

**Layer 4 is not modified.** Layer 6 reads only APPROVED rows.

### Execution order (normal daily flow)
```
run_data.py  →  run_scoring.py  →  run_analysis.py  →  run_portfolio.py  →  run_risk_check.py  →  (Layer 6)
```

`run_risk_check.py` (no flags) runs the full daily pipeline:
factor risk model → pre-trade veto → circuit breakers → factor/correlation/tail monitors → update risk_state.json.

---

## 2. Module Structure

```
ls_equity_fund/risk/
    __init__.py
    db.py                  # New DB tables
    factor_risk_model.py   # Barra-style factor risk decomposition
    pre_trade.py           # 8-check veto gate on position_approvals
    circuit_breakers.py    # P&L-based triggers
    factor_monitor.py      # Factor spread z-score alerts
    correlation_monitor.py # Pairwise correlation + effective N bets
    tail_risk.py           # VIX + credit spread triggers
    stress_test.py         # Historical + synthetic scenarios
    risk_state.py          # Read/write cache/risk_state.json
```

---

## 3. DB Schema (`risk/db.py`)

Two new tables, registered on the shared `data.db` metadata.

### `risk_log`
Records every individual check result.

| Column       | Type    | Notes |
|--------------|---------|-------|
| `id`         | Integer | PK autoincrement |
| `run_date`   | String  | YYYY-MM-DD |
| `check_type` | String  | e.g. `pre_trade`, `circuit_breaker`, `tail_risk` |
| `ticker`     | String  | nullable — NULL for portfolio-level checks |
| `result`     | String  | `APPROVED`, `REJECTED`, `WARNING`, `TRIGGERED` |
| `reason`     | String  | Human-readable explanation |
| `recorded_at`| String  | ISO timestamp |

### `risk_events`
One row per circuit-breaker or tail-risk event that changes portfolio state.

| Column       | Type    | Notes |
|--------------|---------|-------|
| `id`         | Integer | PK autoincrement |
| `event_date` | String  | YYYY-MM-DD |
| `event_type` | String  | `SIZE_DOWN_30`, `CLOSE_ALL`, `KILL_SWITCH`, `REDUCE_GROSS_20`, `REDUCE_GROSS_50` |
| `trigger`    | String  | e.g. `daily_loss_1.5pct`, `vix_35` |
| `detail`     | String  | JSON blob with metrics at trigger time |
| `recorded_at`| String  | ISO timestamp |

No foreign keys, no cascades. Index on `(run_date, check_type)` and `(event_date,)`.

---

## 4. Risk State (`risk/risk_state.py`)

`cache/risk_state.json` is the single source of truth for circuit-breaker memory and
the dashboard (Layer 7). It is read at startup and written at the end of every run.

### Schema
```json
{
  "as_of":                  "2026-05-06",
  "nav_usd":                10000000.0,
  "daily_pnl_usd":          -150000.0,
  "daily_pnl_pct":          -0.015,
  "weekly_pnl_pct":         -0.025,
  "peak_nav_usd":           10500000.0,
  "drawdown_pct":           0.047,
  "halted":                 false,
  "circuit_breaker_state":  "NORMAL",
  "tail_risk_state":        "NORMAL",
  "gross_exposure":         1.42,
  "net_exposure":           0.08,
  "net_beta":               0.12,
  "factor_exposures": {
    "long":   {"momentum_score": 72.1, "...": "..."},
    "short":  {"momentum_score": 34.5, "...": "..."},
    "spread": {"momentum_score": 37.6, "...": "..."},
    "flags":  ["momentum_score"]
  },
  "risk_decomposition": {
    "factor_var_pct":   0.68,
    "specific_var_pct": 0.32,
    "annualised_vol":   0.112,
    "factor_contributions": {"momentum_score": 0.21, "...": "..."}
  },
  "mctr_top5": [
    {"ticker": "NVDA", "weight_pct": 4.8, "mctr_pct": 8.2, "flag": true},
    "..."
  ],
  "correlation_monitor": {
    "long_avg_corr":  0.41,
    "short_avg_corr": 0.55,
    "effective_n_bets": 14.2,
    "alerts": []
  },
  "alerts": [
    {"type": "MCTR_CONCENTRATION", "ticker": "NVDA", "message": "MCTR% 8.2 > 1.5× weight% 4.8"}
  ]
}
```

### API

```python
def load_risk_state(cache_dir: Path) -> dict
def save_risk_state(state: dict, cache_dir: Path) -> None
def is_halted(cache_dir: Path) -> bool  # checks cache/halt.lock existence
def set_halt(cache_dir: Path) -> None   # writes cache/halt.lock
def clear_halt(cache_dir: Path) -> None # deletes cache/halt.lock (--clear-halt CLI)
```

`halt.lock` is a plain text file whose presence = system halted. `clear_halt()` deletes it.
`is_halted()` checks file existence, not risk_state.json, so it is always current.

---

## 5. Factor Risk Model (`risk/factor_risk_model.py`)

### Purpose
Decompose portfolio variance into factor-driven and stock-specific components.
Produce a Barra-style predicted covariance matrix for optional use by Layer 4's MVO.

### Inputs
- `daily_prices` table — 120-day lookback, all portfolio tickers plus universe
- `factor_scores` table — 8 factor columns (0–100 sector ranks) for the score date
- `portfolio_positions` — current open positions with weights

### Algorithm

**Step 1 — Standardise factor exposures**

For each factor k, convert the 0–100 sector rank to a z-score across the universe:

```
F_k,i = (score_k,i - mean(score_k)) / std(score_k)
```

**Step 2 — Rolling cross-sectional regression (120 days)**

For each day t with at least 50 stocks having both returns and factor scores:

```
r_i,t = alpha_t + sum_k(beta_k,t * F_k,i) + eps_i,t
```

OLS via `numpy.linalg.lstsq`. Requires the factor scores to be static (use the current
score date's exposures as a proxy for historical exposures — acceptable for a 120-day
window where factor ranks are slow-moving).

Produces:
- `factor_returns[t, k]` — daily factor return time series (T × 8 matrix)
- `specific_returns[t, i]` — residual per stock per day

**Step 3 — Covariance estimation**

```
F_cov  = annualise(cov(factor_returns))   # 8×8, × 252
spec_var[i] = annualise(var(specific_returns[:, i]))
```

**Step 4 — Portfolio variance decomposition**

Given weight vector `w` (signed: long positive, short negative) and exposure matrix `X` (N×8):

```
factor_var   = w' X F_cov X' w
specific_var = sum_i( w_i^2 * spec_var[i] )
total_var    = factor_var + specific_var
sigma_p      = sqrt(total_var)
```

Factor contributions (pct of total variance):

```
factor_contrib[k] = w' X[:,k] * F_cov[k,:] X' w  / total_var
```

MCTR (Marginal Contribution to Risk):

```
cov_ri_rp[i] = (X F_cov X')[i,:] @ w + spec_var[i] * w[i]
MCTR[i]      = w[i] * cov_ri_rp[i] / sigma_p
```

Flag tickers where `|MCTR_pct| > 1.5 × |weight_pct|`.

**Step 5 — Output**

Returns a `FactorRiskResult` dataclass:

```python
@dataclass
class FactorRiskResult:
    factor_cov: np.ndarray          # 8×8 annualised factor covariance
    specific_var: dict[str, float]  # ticker → annualised specific variance
    factor_returns: pd.DataFrame    # T×8 factor return history
    factor_contributions: dict[str, float]  # factor → pct of total var
    total_vol: float                # annualised portfolio vol
    factor_vol_pct: float           # factor_var / total_var
    specific_vol_pct: float         # specific_var / total_var
    mctr: pd.Series                 # ticker → MCTR value
    mctr_flags: list[str]           # tickers where MCTR% > 1.5× weight%
    predicted_cov: np.ndarray       # N×N predicted covariance = X F X' + diag(spec)
```

**MVO integration:** `predicted_cov` and the ticker list are written to
`cache/predicted_cov_<date>.parquet` and `cache/predicted_cov_latest.parquet`.
Layer 4's `mvo_optimizer.py` can load this instead of the sample covariance when it
exists. This is a read-by-Layer-4, no change to Layer 4 required; the MVO code checks
for the file and falls back to sample covariance if absent.

### Public API

```python
def compute_factor_risk(
    conn: sa.engine.Connection,
    positions_df: pd.DataFrame,
    score_date: str,
    lookback_days: int = 120,
) -> FactorRiskResult
```

---

## 6. Pre-Trade Veto (`risk/pre_trade.py`)

### Purpose
Inspect every PENDING row in `position_approvals` and mark it APPROVED or REJECTED.
Closing/covering trades (target_shares ≈ 0) are always APPROVED without checks.

### Inputs (all computed inside the module)
- `position_approvals` — PENDING rows from today's `rebalance_date`
- `portfolio_positions` — current open positions (pre-trade state)
- `daily_prices` — for ADV and pairwise correlations
- `earnings_calendar` table — for earnings blackout
- `factor_scores` table — for sector aggregation
- Risk state JSON — for gross/net/beta current values
- `halt.lock` — if present, reject all opening trades immediately

### The 8 Checks

Evaluated in order. Any failure → REJECTED (and remaining checks are still logged).
Closing/covering trades skip checks 2–8 (only check 1 applies).

| # | Check | Condition for REJECTION |
|---|-------|------------------------|
| 1 | **Halt lock** | `cache/halt.lock` exists |
| 2 | **Earnings blackout** | Earnings within ±5 days → full position blocked (50% size cut applied as a sizing adjustment, not a rejection — trade is resized to 50% of target then approved) |
| 3 | **Liquidity** | Trade value > 5% of 20-day ADV for that ticker |
| 4 | **Position size** | Resulting position > 5% of NAV (abs weight) |
| 5 | **Sector concentration** | Resulting gross sector exposure > 25% of NAV |
| 6 | **Gross / net** | Resulting gross > 165% OR net outside [−10%, +15%] |
| 7 | **Net beta** | Resulting \|net beta\| > 0.20 |
| 8 | **Pairwise correlation** | Correlation of new ticker vs any existing same-book position over 60 days > 0.80 |

Note on check 2: The earnings blackout applies a **50% size reduction** rather than a
full rejection. The trade is modified (delta_shares halved, target_shares adjusted) and
marked APPROVED with a `BLACKOUT_REDUCED` note in the reason column.

### Implementation

```python
def run_pre_trade(
    conn: sa.engine.Connection,
    score_date: str,
    config: dict,
    cache_dir: Path,
) -> pd.DataFrame:
    """
    Returns DataFrame with columns:
        ticker, action, result (APPROVED|REJECTED), reason
    Updates position_approvals.status in the DB.
    """
```

ADV computation: 20-day average of (close × volume) from `daily_prices`, loaded once
and cached for the run. Beta computation reuses `portfolio.beta.compute_betas`.

Every check outcome is logged to `risk_log` with check_type=`pre_trade`.

---

## 7. Circuit Breakers (`risk/circuit_breakers.py`)

### Purpose
Monitor P&L and drawdown; automatically reduce exposures or halt trading.

### P&L Computation

Daily P&L is computed from `portfolio_history`:

```
daily_pnl_pct = (today_total_market_value - yesterday_total_market_value) / nav_usd
```

Where `today_total_market_value = sum(|market_value|)` accounting for sign on shorts.

Weekly P&L uses the snapshot 5 trading days ago from `portfolio_history`.

Drawdown:

```
peak_nav = max(daily nav values ever seen, stored in risk_state.json)
drawdown = (peak_nav - current_nav) / peak_nav
```

If `portfolio_history` has no prior data, P&L is 0 and drawdown is 0.

### Trigger Table

| Trigger | Action | DB event_type |
|---------|--------|---------------|
| Daily loss > 1.5% | Resize all PENDING APPROVED trades to 70% of target shares | `SIZE_DOWN_30` |
| Daily loss > 2.5% | Mark all PENDING non-closure trades REJECTED; log `CLOSE_ALL` | `CLOSE_ALL` |
| Weekly loss > 4% | Same as daily 1.5% (SIZE_DOWN 30%) | `SIZE_DOWN_30` |
| Drawdown > 8% | Write `cache/halt.lock`; reject all non-closure trades | `KILL_SWITCH` |
| Single position > 3% NAV | Force-close: set position_approvals entry to target_shares=0, action=SELL/COVER | `FORCE_CLOSE` |

**Priority:** Evaluate in order drawdown → daily loss → weekly loss → single position.
KILL_SWITCH overrides SIZE_DOWN — do not apply SIZE_DOWN if KILL_SWITCH fires.
SIZE_DOWN adjustments run after pre-trade veto (they modify already-APPROVED rows).

### Public API

```python
def run_circuit_breakers(
    conn: sa.engine.Connection,
    score_date: str,
    nav_usd: float,
    risk_state: dict,
    cache_dir: Path,
) -> dict:
    """
    Returns updated risk_state dict.
    Modifies position_approvals rows in-place.
    Logs to risk_events and risk_log.
    """
```

---

## 8. Factor Monitor (`risk/factor_monitor.py`)

### Purpose
Detect when long-minus-short factor spreads are unusually wide, indicating crowded or
risk-elevated positioning.

### Algorithm

1. Load factor spread (long book weighted avg minus short book weighted avg) for today
   from `portfolio_positions` + `factor_scores`. Reuses `portfolio.factor_exposure.compute_exposures`.
2. Load historical spreads: pull the last 252 trading days of `factor_scores` rows,
   compute cross-sectional std for each factor across the whole universe on each date.
3. Z-score today's spread: `z = (spread - mean_hist) / std_hist` per factor.
4. Alert if `|z| > 1.5`.
5. Cross-reference with crowding flags from `factors.crowding` (already computed in
   Layer 2 — query `factor_scores` where `crowding_flag IS NOT NULL` or use the
   config `crowding.deviation_threshold`). If a flagged factor also has `|z| > 1.5`,
   upgrade to HIGH priority alert.

### Output

Returns list of alert dicts appended to `risk_state["alerts"]`:

```python
{"type": "FACTOR_SPREAD", "factor": "momentum_score", "z": 2.1, "priority": "HIGH"}
```

Every alert is also logged to `risk_log` with check_type=`factor_monitor`.

---

## 9. Correlation Monitor (`risk/correlation_monitor.py`)

### Purpose
Measure within-book pairwise correlations and estimate effective diversification.

### Algorithm

1. Load current long book and short book tickers from `portfolio_positions`.
2. Pull 60 days of adj_close returns for all tickers from `daily_prices`.
3. Compute pairwise correlation matrix per book (long separately from short).
4. `avg_long_corr` = mean of upper triangle of long book correlation matrix.
5. `avg_short_corr` = mean of upper triangle of short book correlation matrix.
6. Alert if `avg_long_corr > 0.60` or `avg_short_corr > 0.60`.

**Effective number of bets** (both books combined):

Eigendecompose the combined portfolio correlation matrix. Let `λ_k` be eigenvalues:

```
weights    = λ_k / sum(λ_k)
entropy    = -sum(weights * log(weights))
eff_n_bets = exp(entropy)
```

This is stored in `risk_state["correlation_monitor"]["effective_n_bets"]`.

### Output

```python
{
    "long_avg_corr":    float,
    "short_avg_corr":   float,
    "effective_n_bets": float,
    "alerts":           list[dict],
}
```

---

## 10. Tail Risk Monitor (`risk/tail_risk.py`)

### Purpose
Respond to market stress (VIX spikes, credit spread widening) by reducing gross exposure.
These triggers modify APPROVED position_approvals rows (same SIZE_DOWN mechanism as
circuit breakers), they do NOT reject entirely.

### VIX

VIX prices are already in `daily_prices` under ticker `^VIX`. Pull today's close.

| VIX Level | Action |
|-----------|--------|
| >= 35 | Reduce all APPROVED non-closure target shares by 50% (`REDUCE_GROSS_50`) |
| >= 25 | Reduce all APPROVED non-closure target shares by 20% (`REDUCE_GROSS_20`) |
| < 25 | No action |

### Credit Spread

Two sources in priority order:

1. **FRED** (`FRED_API_KEY` env var): pull `BAMLH0A0HYM2` (ICE BofA US High Yield
   Option-Adjusted Spread) for the last 252 trading days. Cache response to
   `cache/fred_hy_spread.parquet`, refresh daily.
2. **Fallback — HYG proxy**: HYG price is already in `daily_prices`. Use 
   `z_score = (today_hyg - mean_60d_hyg) / std_60d_hyg`. Widen signal: negative HYG
   z-score (HYG falling = spreads widening) maps to positive spread z-score.

Alert when spread z-score (vs 252-day mean/std) >= +1.0 sigma. Action: `REDUCE_GROSS_20`.

If both VIX >= 35 AND spread >= 1 sigma simultaneously, the VIX action dominates
(already 50% reduction). Do not double-reduce.

### Output

`tail_risk_state` string in risk_state: `NORMAL`, `CAUTION` (VIX 25+), or `STRESS` (VIX 35+).

All actions logged to `risk_events` and `risk_log`.

---

## 11. Stress Testing (`risk/stress_test.py`)

### Purpose
Estimate portfolio P&L under historical and synthetic shock scenarios.

### Historical Scenarios

Actual stock-level returns are fetched from yfinance for the scenario period and cached
to `cache/stress/<scenario_name>.parquet`. On subsequent runs the parquet is loaded
directly (no yfinance call). The cache is considered stale if the file is older than
30 days.

| Scenario | Period | Tickers |
|----------|--------|---------|
| `financial_crisis_2008` | 2008-09-01 → 2009-03-31 | Current portfolio tickers |
| `covid_crash_2020` | 2020-02-01 → 2020-04-30 | Current portfolio tickers |
| `rate_hike_2022` | 2022-01-01 → 2022-10-31 | Current portfolio tickers |

For each scenario:
1. Compute cumulative total return per ticker over the period (or NaN if ticker didn't exist).
2. Map returns onto current positions using portfolio weights.
3. Split contributions: long book P&L and short book P&L separately.
4. Report total scenario P&L = long_pnl + short_pnl.

Tickers without data default to a sector-average return from those that do exist.

### Synthetic Scenarios

These use parametric shocks applied to current positions. No historical data needed.

| Scenario | Shock |
|----------|-------|
| `sector_shock` | Most concentrated sector (by gross weight) drops 30%. Other sectors flat. |
| `momentum_reversal` | Top quintile of `momentum_score` drops 20%; bottom quintile rises 20%. Proxy for 2007 quant quake. |
| `short_squeeze` | All short positions rise 30% simultaneously. |

### Output Format

```python
@dataclass
class ScenarioResult:
    name:         str
    period:       str         # e.g. "2008-09-01 to 2009-03-31" or "synthetic"
    total_pnl_usd: float
    total_pnl_pct: float
    long_pnl_usd:  float
    short_pnl_usd: float
    worst_position: str       # ticker with largest loss contribution
```

Printed as a table to stdout; not written to DB (on-demand reporting only).

### Public API

```python
def run_stress_tests(
    positions_df: pd.DataFrame,
    score_date: str,
    config: dict,
    cache_dir: Path,
    scenarios: list[str] | None = None,  # None = all
) -> list[ScenarioResult]
```

---

## 12. Entry Point (`run_risk_check.py`)

### CLI

```
python run_risk_check.py                  # full daily pipeline
python run_risk_check.py --stress         # full pipeline + stress tests
python run_risk_check.py --tail-only      # tail risk monitors only (no pre-trade)
python run_risk_check.py --pre-trade-only # only run pre-trade veto
python run_risk_check.py --clear-halt     # clear kill switch lock file and exit
python run_risk_check.py --date 2026-05-01
python run_risk_check.py --whatif         # run all checks but don't commit changes to DB
```

### Execution Sequence (full run)

1. Load config and resolve score_date
2. Check `halt.lock` — if present and `--clear-halt` not given, print warning and exit
3. Load risk_state.json (or initialise empty if missing)
4. Load `portfolio_positions` (current state) and `position_approvals` (PENDING for today)
5. Load prices (120-day window for factor risk model; 60-day for correlation)
6. **Factor risk model** — compute and write `cache/predicted_cov_<date>.parquet`; update risk_state
7. **Pre-trade veto** — stamp PENDING rows as APPROVED/REJECTED; log to risk_log
8. **Circuit breakers** — evaluate P&L; apply SIZE_DOWN or KILL_SWITCH; log events
9. **Tail risk** — evaluate VIX/credit; apply REDUCE_GROSS; log events
10. **Factor monitor** — compute z-scores; append to risk_state alerts
11. **Correlation monitor** — compute effective N bets; append to risk_state alerts
12. **Save risk_state.json**
13. **Print summary** (see below)
14. If `--stress`: run stress tests and print results

### Console Output (normal run)

```
=== Layer 5 Risk Management — 2026-05-06 ===

Portfolio Risk Decomposition:
  Annualised vol   :  11.2%
  Factor risk      :  68%  (of total variance)
  Specific risk    :  32%
  Top factor       :  momentum_score  21%

  MCTR concentrations (flagged):
    NVDA   weight 4.8%  MCTR 8.2%  ⚠

Pre-Trade Veto (18 pending trades):
  APPROVED : 15
  REJECTED :  2  (AAPL — liquidity, DE — sector limit)
  REDUCED  :  1  (MSFT — earnings blackout, 50% size cut)

Circuit Breakers: NORMAL
  Daily P&L    :  +0.32%
  Weekly P&L   :  -1.12%
  Drawdown     :   3.8%

Tail Risk: CAUTION
  VIX          :  27.4  → REDUCE_GROSS_20 applied (13 trades resized)
  Credit spread:  +0.8σ (below threshold)

Factor Monitor:
  momentum_score  z=+2.1  HIGH PRIORITY  (crowding + spread)
  quality_score   z=+1.7

Correlation Monitor:
  Long book avg corr  : 0.48  OK
  Short book avg corr : 0.61  ⚠ ALERT
  Effective N bets    : 14.2

Alerts: 3
  [HIGH]  FACTOR_SPREAD: momentum_score z=2.1 (crowding confirmed)
  [MED]   CORR: short book avg correlation 0.61 > 0.60 threshold
  [MED]   MCTR: NVDA MCTR% 8.2 > 1.5× weight% 4.8

=== Done ===
```

---

## 13. Config Additions (`config.yaml`)

New `risk:` section:

```yaml
risk:
  factor_risk_lookback_days:   120
  pre_trade:
    liquidity_adv_pct:         0.05
    max_position_pct:          0.05
    max_sector_pct:            0.25
    max_gross:                 1.65
    net_min:                  -0.10
    net_max:                   0.15
    max_net_beta:              0.20
    max_pairwise_corr:         0.80
    corr_lookback_days:        60
    earnings_blackout_days:    5
  circuit_breakers:
    daily_loss_size_down_pct:  0.015
    daily_loss_close_all_pct:  0.025
    weekly_loss_pct:           0.040
    drawdown_kill_pct:         0.080
    max_single_position_pct:   0.030
  tail_risk:
    vix_caution:               25
    vix_stress:                35
    credit_spread_sigma:       1.0
    credit_lookback_days:      252
  factor_monitor:
    alert_z_threshold:         1.5
  correlation_monitor:
    alert_avg_corr:            0.60
    lookback_days:             60
  stress:
    cache_ttl_days:            30
    scenarios:
      - financial_crisis_2008
      - covid_crash_2020
      - rate_hike_2022
      - sector_shock
      - momentum_reversal
      - short_squeeze
```

These values mirror the limits already enforced in Layer 4's portfolio config. The risk
layer uses its own `risk:` section so the two layers can diverge without coupling.

---

## 14. Testing (`tests/test_risk_*.py`)

Each module gets its own test file. All tests use synthetic in-memory data (no DB or
network). Key coverage:

| File | Tests |
|------|-------|
| `test_risk_pre_trade.py` | Each of the 8 checks in isolation; closing trades always pass; earnings blackout size reduction |
| `test_risk_circuit_breakers.py` | Each trigger threshold; KILL_SWITCH creates halt.lock; SIZE_DOWN modifies target_shares |
| `test_risk_factor_risk.py` | Variance decomposition sums to total; MCTR flagging; predicted_cov shape |
| `test_risk_tail_risk.py` | VIX thresholds; HYG proxy z-score; no double-reduction |
| `test_risk_correlation.py` | Effective N bets formula; high-corr alert |
| `test_risk_stress_test.py` | Synthetic scenarios produce correct sign (short squeeze hurts short book) |
| `test_risk_state.py` | halt.lock create/clear/check; JSON round-trip |

---

## 15. Dependencies

No new packages required. Uses existing stack:
- `numpy`, `pandas`, `scipy` — factor regression, covariance, eigendecomposition
- `sqlalchemy` — DB reads/writes
- `yfinance` — stress test historical returns (cached)
- `requests` — FRED API (optional)
- `pyarrow` — parquet cache for predicted covariance and stress scenario returns

`pyarrow` may need adding to `requirements.txt` if not already present (check before implementing).
