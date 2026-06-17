# Meridian Capital Partners — Layer 5: Risk Management

Build Layer 5 of the Meridian Capital Partners hedge fund. Layers 1–4 are built.  
Build risk management with **ABSOLUTE VETO POWER** plus Barra-style factor risk model.

---

## Factor Risk Model

### 1. Factor Risk Model (`risk/factor_risk_model.py`)

Barra-style cross-sectional regression. For each day `t` in 120-day lookback:

```
r_i,t = alpha_t + sum_k beta_k,t * F_k,i + epsilon_i,t
```

Where `F_k,i` = stock `i` standardized factor exposure (z-scored from 0–100 sector ranks).

**Produces:**
- Factor returns (daily series)
- Factor covariance matrix (annualized)
- Specific variance per stock (annualized)

**Portfolio variance decomposition:**

| Term | Formula |
|------|---------|
| `factor_var` | `exp * F * exp` |
| `specific_var` | `sum(w_i^2 * spec_var_i)` |
| `total_var` | `factor_var + specific_var` |
| `MCTR_i` | `w_i * cov(r_i, r_p) / sigma_p` |

- Flag where `MCTR%` > 1.5x `weight%`
- Feed predicted cov matrix (`X*F*X + diag(specific)`) to Layer 4 MVO optimizer

---

## Risk Checks (Absolute Veto — No Override)

### 2. Pre-Trade Veto (`risk/pre_trade.py`) — 8 checks, ANY failure = REJECT

| # | Check |
|---|-------|
| 1 | Halt lock exists? |
| 2 | Earnings blackout (5d = 50% size cut) |
| 3 | Liquidity <= 5% ADV |
| 4 | Position <= 5% AUM |
| 5 | Sector <= 25% |
| 6 | Gross <= 165%, net [−10%, +15%] |
| 7 | \|net beta\| <= 0.20 |
| 8 | Pairwise correlation <= 0.80 with existing positions |

- Closing/covering trades always approved
- Log every rejection with timestamp and reason

---

### 3. Circuit Breakers (`risk/circuit_breakers.py`) — fire on actual dollar losses

| Trigger | Action |
|---------|--------|
| Daily loss > 1.5% | `SIZE_DOWN 30%` |
| Daily loss > 2.5% | `CLOSE_ALL_TODAY` |
| Weekly loss > 4% | `SIZE_DOWN 30%` |
| Drawdown > 8% | `KILL_SWITCH` (lock file, `--clear-halt`) |
| Single position > 3% NAV | Force-close immediately |

---

### 4. Factor Monitor (`risk/factor_monitor.py`)

- Z-score each factor spread (long minus short) vs universe cross-sectional std
- Alert when `|z| > 1.5` sigma
- Cross-check vs crowding warnings = **HIGH** priority alert

---

### 5. Correlation Monitor (`risk/correlation_monitor.py`)

- 60-day rolling pairwise correlations within each book
- Alert if avg within-book > 0.60
- Effective number of bets: `exp(entropy(eigenvalue_distribution))`

---

### 6. Tail Risk Monitor (`risk/tail_risk.py`)

| Trigger | Action |
|---------|--------|
| VIX >= 25 | `REDUCE_GROSS_20%` |
| VIX >= 35 | `REDUCE_GROSS_50%` |
| Credit spread z-score >= 1 sigma widening | `REDUCE_GROSS_20%` |

- No override possible
- If `FRED_API_KEY` available, pull `BAMLH0A0HYM2` for actual high-yield credit spread

---

### 7. Stress Testing (`risk/stress_test.py`) — 6 scenarios

**Historical:**

| Scenario | Period |
|----------|--------|
| 2008 Financial Crisis | Sep 2008 – Mar 2009 |
| 2020 Covid Crash | Feb – Apr 2020 |
| 2022 Rate Hikes | Jan – Oct 2022 |

Use actual stock-level returns from yfinance, cache parquet.

**Synthetic:**

| Scenario | Description |
|----------|-------------|
| Sector Shock | −30% most concentrated sector |
| Momentum Reversal | Top quintile −20%, bottom +20% (the quant quake) |
| Short Squeeze | All shorts +30% simultaneously |

Report estimated P&L ($, %) broken into long book and short book contributions.

---

### 8. Risk State (`risk/risk_state.py`)

Maintain `cache/risk_state.json` with:
- Daily/weekly P&L
- Drawdown
- Circuit breaker usage
- Factor exposures
- Risk decomposition
- Per-factor contributions
- Top MCTR positions
- Alerts

---

## Entry Point

**`run_risk_check.py`**

| Flag | Description |
|------|-------------|
| `--stress` | Run stress test scenarios |
| `--tail-only` | Check tail risk monitors only |
| `--clear-halt` | Clear kill switch halt lock |
