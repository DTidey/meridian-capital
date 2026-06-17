# Factor Scoring Engine

**Spec file:** `docs/specs/02-factor-scoring.md`
**Status:** Done
**Date:** 2026-06-15

## Purpose

Layer 2 reads from the Layer 1 PostgreSQL tables and produces a daily scored universe. Every S&P 500 stock receives 27 sub-factor scores across 8 factor groups and one composite score, each expressed as a 0-100 percentile rank within its GICS sector, with LONG/SHORT labels assigned to the top and bottom quintiles.

## Acceptance criteria

- AC1: The system computes 27 sub-factor scores across 8 factors (momentum 6, value 6, quality 8, growth 5, revisions 3, short interest 3, insider 2, institutional 3) for every ticker in the S&P 500 universe, expressed as sector-percentile ranks on a 0-100 scale.
- AC2: Tickers with NaN raw values receive the sector median score (50.0) after ranking; if a sector has fewer than `min_sector_size` (default 5) non-NaN tickers for a sub-factor, the system falls back to universe-wide ranking and logs a warning.
- AC3: The composite score is computed as a weighted sum of the 8 factor scores (weights from config, validated to sum to 1.0 at startup) and re-ranked within sector; tickers with composite >= 80 are labelled LONG and composite <= 20 are labelled SHORT.
- AC4: The regime module reads the latest ^VIX close and adjusts factor weights — LOW_VOL (VIX < 15): momentum 0.28, value 0.10; HIGH_VOL (VIX > 25): quality 0.28, value 0.22, momentum 0.10 — re-normalising to sum to 1.0; if VIX data is unavailable, NORMAL weights are used with a logged warning.
- AC5: The revisions factor defaults all three sub-factors to 50.0 when fewer than 30 days of `analyst_estimates` history exist for a ticker (degenerate mode), and logs a warning if more than 50% of the universe is degenerate.
- AC6: The short interest factor stores LONG-convention scores (lower short interest = higher score); `composite.py` applies `100 - score` when computing the SHORT composite, without storing a separate column.
- AC7: The insider factor weights CEO/CFO open-market transactions at 3x versus other insiders, and defaults both sub-factors to 50.0 when no open-market transactions (is_open_market = 1) exist in the 90-day window.
- AC8: Crowding detection computes 60-day rolling pairwise Pearson correlations of factor return series and flags any pair where the deviation from its academic baseline exceeds 0.40; when fewer than 60 days of `factor_scores` history exist, the step is skipped with an informational log.
- AC9: All scoring results, the resolved regime state, and crowding flags are upserted into `factor_scores`, `regime_state`, and `crowding_flags` tables and also written to `output/scored_universe_latest.csv`.
- AC10: The entry point `run_scoring.py` accepts `--ticker`, `--date`, and `--no-crowding` flags and prints a structured summary including regime, LONG/SHORT counts, and degenerate factor warnings.

## Security considerations

- Auth/authz impact: Layer 2 reads from and writes to the shared PostgreSQL database; database credentials control all access.
- Secrets or credential handling: No additional API keys introduced in Layer 2; `DATABASE_URL` is read from environment variables only, never hardcoded.
- Network or external service impact: No outbound network calls; all data is read from the Layer 1 PostgreSQL tables.
- Input handling: All input comes from the trusted internal database; no external user input is processed.
- No meaningful security impact beyond the above.

## Test guidance

- AC1 -> `tests/test_momentum.py`, `tests/test_value.py`, `tests/test_quality.py`, `tests/test_growth.py`, `tests/test_revisions.py`, `tests/test_short_interest.py`, `tests/test_insider.py`, `tests/test_institutional.py`
- AC2 -> `tests/test_composite.py`, `tests/test_loader.py`
- AC3 -> `tests/test_composite.py`
- AC4 -> `tests/test_regime_weights.py`
- AC5 -> `tests/test_revisions.py`
- AC6 -> `tests/test_short_interest.py`, `tests/test_composite.py`
- AC7 -> `tests/test_insider.py`
- AC8 -> `tests/test_crowding.py`
- AC9 -> `tests/test_scoring_db.py`
- AC10 -> `tests/test_scoring_db.py`

---

# Meridian Capital Partners — Layer 2: Factor Scoring Engine
## Implementation Specification

**Date:** 2026-05-05  
**Status:** Complete  
**Depends on:** Layer 1 (`data/`) — all tables populated in PostgreSQL

---

## 1. Overview

Layer 2 reads from the Layer 1 PostgreSQL tables and produces a daily scored universe. Every stock in the S&P 500 receives 27 sub-factor scores, 8 factor scores, and one composite score, each expressed as a **0–100 percentile rank within its GICS sector**. The composite identifies the top quintile as LONG candidates and the bottom quintile as SHORT candidates.

Three additional components run after the main scoring loop: regime-conditional weight adjustment (VIX-based), crowding detection (factor-return correlations), and output generation (CSV + PostgreSQL).

---

## 2. Module Structure

```
ls_equity_fund/
├── run_scoring.py          # Layer 2 entry point
└── factors/
    ├── __init__.py
    ├── db.py               # New table definitions + write helpers
    ├── loader.py           # Pull Layer 1 data into DataFrames
    ├── momentum.py         # 6 sub-factors
    ├── value.py            # 6 sub-factors
    ├── quality.py          # 8 sub-factors
    ├── growth.py           # 5 sub-factors
    ├── revisions.py        # 3 sub-factors (degenerate until 30d data)
    ├── short_interest.py   # 3 sub-factors
    ├── insider.py          # 2 scored sub-factors + filter rule
    ├── institutional.py    # 3 sub-factors
    ├── composite.py        # Weighted blend, LONG/SHORT labelling
    ├── regime_weights.py   # VIX-based weight adjustment
    └── crowding.py         # 60-day factor-return correlation monitor
```

`factors/db.py` imports the global `metadata` object from `data.db` and registers new tables on it. This means the existing `initialise_schema(engine)` call in `run_data.py` (and the test fixtures) will automatically create Layer 2 tables when they are imported — no separate DDL step is required.

---

## 3. Database Schema

### 3.1 New tables

#### `factor_scores`
Stores the full scored universe for each run. Primary key is `(ticker, score_date)`.

| Column | Type | Description |
|--------|------|-------------|
| `ticker` | String | S&P 500 constituent |
| `score_date` | String | Date of scoring run (ISO 8601) |
| `sector` | String | GICS sector (denormalised for fast queries) |
| `regime` | String | `LOW_VOL` / `NORMAL` / `HIGH_VOL` |
| *(momentum: 6 columns)* | Float | `mom_12_1`, `mom_6m`, `mom_3m`, `mom_accel`, `mom_52w_high`, `mom_rel_strength` |
| `momentum_score` | Float | Equal-weighted mean of 6 sub-factors, sector-percentile ranked |
| *(value: 6 columns)* | Float | `val_fwd_earn_yield`, `val_book_to_price`, `val_fcf_yield`, `val_ev_ebitda_inv`, `val_shareholder_yield`, `val_sales_to_ev` |
| `value_score` | Float | Equal-weighted mean of 6 sub-factors, sector-percentile ranked |
| *(quality: 8 columns)* | Float | `qual_roe_stability`, `qual_gm_level`, `qual_gm_trend`, `qual_de_inv`, `qual_cfo_to_ni`, `qual_accruals_inv`, `qual_piotroski`, `qual_altman_z` |
| `quality_score` | Float | Equal-weighted mean of 8 sub-factors, sector-percentile ranked |
| *(growth: 5 columns)* | Float | `grw_rev_yoy`, `grw_earn_yoy`, `grw_rev_accel`, `grw_rd_intensity`, `grw_fcf_yoy` |
| `growth_score` | Float | Equal-weighted mean of 5 sub-factors, sector-percentile ranked |
| *(revisions: 3 columns)* | Float | `rev_30d`, `rev_60d`, `rev_90d` |
| `revisions_score` | Float | Equal-weighted mean of available deltas, sector-percentile ranked; defaults to 50.0 when degenerate |
| *(short interest: 3 columns)* | Float | `si_pct_float`, `si_days_to_cover`, `si_change` |
| `short_interest_score` | Float | Sector-percentile ranked (direction-aware: see §4.6) |
| *(insider: 2 columns)* | Float | `ins_net_flow`, `ins_cluster_flag` |
| `insider_score` | Float | Equal-weighted, sector-percentile ranked; defaults to 50.0 when no data |
| *(institutional: 3 columns)* | Float | `inst_funds_holding`, `inst_net_share_change`, `inst_simultaneous_open` |
| `institutional_score` | Float | Equal-weighted, sector-percentile ranked |
| `composite_score` | Float | Weighted blend of 8 factors, re-ranked within sector |
| `direction` | String | `LONG` / `SHORT` / `NEUTRAL` |
| `computed_at` | String | Timestamp |

All sub-factor columns hold **sector-percentile ranks (0–100)**, not raw values. Raw values are computed in-memory and are not persisted (they can always be recomputed from Layer 1).

Primary key: `(ticker, score_date)`  
Index: `(score_date)` for fast "latest run" queries.

---

#### `regime_state`
One row per scoring run, recording the VIX value and resolved regime.

| Column | Type | Description |
|--------|------|-------------|
| `score_date` | String | PK |
| `vix_close` | Float | ^VIX close used to resolve regime |
| `regime` | String | `LOW_VOL` / `NORMAL` / `HIGH_VOL` |
| `computed_at` | String | Timestamp |

---

#### `crowding_flags`
One row per factor-pair per detection run.

| Column | Type | Description |
|--------|------|-------------|
| `score_date` | String | Date of detection |
| `factor_a` | String | e.g. `momentum` |
| `factor_b` | String | e.g. `value` |
| `rolling_corr` | Float | 60-day rolling pairwise correlation |
| `baseline_corr` | Float | Academic baseline (e.g. −0.30 for mom/val) |
| `deviation` | Float | `abs(rolling_corr − baseline_corr)` |
| `flagged` | Integer | 1 if deviation > 0.4 |
| `computed_at` | String | Timestamp |

Primary key: `(score_date, factor_a, factor_b)`

---

### 3.2 Config additions (`config.yaml`)

```yaml
scoring:
  score_date: null           # null = today; override for back-dated runs
  output_csv: output/scored_universe_latest.csv
  long_quintile_threshold: 80    # composite >= 80 → LONG
  short_quintile_threshold: 20   # composite <= 20 → SHORT
  min_sector_size: 5             # fall back to universe-wide rank if sector < 5
  regime_conditional_weights: true

  factor_weights:
    momentum:      0.20
    quality:       0.20
    value:         0.15
    revisions:     0.15
    insider:       0.10
    growth:        0.10
    short_interest: 0.05
    institutional: 0.05

  regime_weights:
    low_vol:
      vix_below: 15
      momentum:  0.28
      value:     0.10
    high_vol:
      vix_above: 25
      quality:   0.28
      value:     0.22
      momentum:  0.10

  crowding:
    window_days: 60
    deviation_threshold: 0.40
    baselines:
      momentum_value:   -0.30
      momentum_quality:  0.10

  sector_etf_map:
    Information Technology:   XLK
    Financials:               XLF
    Health Care:              XLV
    Energy:                   XLE
    Industrials:              XLI
    Communication Services:   XLC
    Consumer Discretionary:   XLY
    Consumer Staples:         XLP
    Materials:                XLB
    Real Estate:              XLRE
    Utilities:                XLU
```

---

## 4. Factor Specifications

### 4.1 Data Loading (`factors/loader.py`)

`loader.py` exposes a single public function:

```python
def load_scoring_data(conn, config, score_date) -> dict[str, pd.DataFrame]
```

Returns a dict of DataFrames keyed by name. All date filtering is applied here, keeping the factor modules stateless and easy to test.

| Key | Source table(s) | Rows | Notes |
|-----|----------------|------|-------|
| `universe` | `sp500_universe` | One per ticker | Sector mapping |
| `prices` | `daily_prices` | Rolling 756 trading days (~3 years) | Sorted by date ascending |
| `fundamentals` | `fundamentals` | Up to 12 quarters per ticker | `period_type = 'quarterly'` |
| `short_interest` | `short_interest` | Up to 90 days per ticker | For change calculation |
| `estimates` | `analyst_estimates` | Up to 90 days per ticker | For revision deltas |
| `insider` | `insider_transactions` + `insider_cluster_flags` | 90-day window | Pre-filtered to codes P and S only |
| `institutional` | `institutional_summary` | Latest 2 report dates per ticker | For change vs prior quarter |
| `vix` | `daily_prices` where ticker = '^VIX' | Latest close | For regime detection |

---

### 4.2 Momentum (`factors/momentum.py`) — 6 sub-factors

All returns use `adj_close`. Skip tickers with fewer than 252 trading days of history (assign NaN → sector median after ranking).

| Column | Calculation |
|--------|-------------|
| `mom_12_1` | Return from 252 days ago to 21 days ago (skip recent month) |
| `mom_6m` | Return from 126 days ago to today |
| `mom_3m` | Return from 63 days ago to today |
| `mom_accel` | `mom_3m` − return from 126→63 days ago |
| `mom_52w_high` | `latest_close / max(adj_close over past 252 days)` |
| `mom_rel_strength` | `mom_6m` − 6-month return of the ticker's sector ETF (from `sector_etf_map`) |

Sector ETF returns are computed the same way as stock returns and are not sector-ranked (they serve as a benchmark, not a ranked signal).

---

### 4.3 Value (`factors/value.py`) — 6 sub-factors

Market cap = `latest_close × shares_outstanding`. EV = `market_cap + total_debt − cash`. All pulled from the most recent quarterly fundamentals row, cross-joined with latest price.

| Column | Calculation |
|--------|-------------|
| `val_fwd_earn_yield` | `eps_estimate_fwd / latest_close` (forward earnings yield = 1/forward P/E) |
| `val_book_to_price` | `(total_equity / shares_outstanding) / latest_close` |
| `val_fcf_yield` | `fcf / market_cap` |
| `val_ev_ebitda_inv` | `1 / (EV / ebit)` — inverted so higher is cheaper. Use `NaN` when EV or EBIT ≤ 0 |
| `val_shareholder_yield` | `(abs(dividends_paid) + abs(buybacks)) / market_cap` |
| `val_sales_to_ev` | `revenue / EV` — `NaN` when EV ≤ 0 |

Higher raw value = cheaper stock = higher rank. All sub-factors naturally scale in the "higher is better" direction so no inversion is needed at the ranking step.

---

### 4.4 Quality (`factors/quality.py`) — 8 sub-factors

Requires up to 12 quarters of fundamentals. Assign `NaN` for any sub-factor where there is insufficient data; these tickers receive the sector median (50) after ranking.

| Column | Calculation |
|--------|-------------|
| `qual_roe_stability` | `−stdev(roe over last 12 quarters)` — higher (less negative) = more stable |
| `qual_gm_level` | `gross_margin` from the most recent quarter |
| `qual_gm_trend` | `gross_margin[latest] − gross_margin[4 quarters ago]` |
| `qual_de_inv` | `−(total_debt / total_equity)` — inverted; lower debt = higher rank |
| `qual_cfo_to_ni` | `cfo / net_income`; set `NaN` when `net_income = 0` |
| `qual_accruals_inv` | `−((net_income − cfo) / total_assets)` — inverted; lower accruals = higher rank |
| `qual_piotroski` | 9-signal Piotroski F-Score (1–9); computed from 2 consecutive quarters |
| `qual_altman_z` | `1.2×(WC/TA) + 1.4×(RE/TA) + 3.3×(EBIT/TA) + 0.6×(MktCap/TL) + 1.0×(Sales/TA)` |

**Piotroski signals** (each binary: 1 if condition met, else 0):
1. ROA > 0
2. CFO > 0
3. ROA increased vs prior quarter
4. CFO > net income
5. Debt/equity decreased vs prior quarter
6. Current ratio increased vs prior quarter
7. No dilution (shares outstanding did not increase)
8. Gross margin increased vs prior quarter
9. Asset turnover increased vs prior quarter

Sum = F-Score (0–9). Stored as raw score; ranked within sector as any other sub-factor. For dashboard annotation: green ≥ 7, amber ≤ 3.

**Altman Z-Score**: `WC` = working capital, `TA` = total assets, `RE` = retained earnings, `EBIT` = operating income, `MktCap` = market cap, `TL` = total liabilities, `Sales` = revenue. Set `NaN` when any required field is missing. For dashboard annotation: > 2.99 green, 1.81–2.99 grey, < 1.81 amber.

---

### 4.5 Growth (`factors/growth.py`) — 5 sub-factors

YoY comparisons use the same calendar quarter from one year prior (e.g. Q1 2025 vs Q1 2024). If the prior-year period is absent, assign `NaN`.

| Column | Calculation |
|--------|-------------|
| `grw_rev_yoy` | `(revenue[latest] − revenue[4q ago]) / abs(revenue[4q ago])` |
| `grw_earn_yoy` | `(net_income[latest] − net_income[4q ago]) / abs(net_income[4q ago])` |
| `grw_rev_accel` | `grw_rev_yoy[latest] − grw_rev_yoy[4q ago]` — acceleration in revenue growth |
| `grw_rd_intensity` | `rd_expense / revenue`; `NaN` where revenue ≤ 0 |
| `grw_fcf_yoy` | `(fcf[latest] − fcf[4q ago]) / abs(fcf[4q ago])`; `NaN` when base is 0 |

---

### 4.6 Estimate Revisions (`factors/revisions.py`) — 3 sub-factors

Revisions are computed from the `analyst_estimates` table, which accumulates daily snapshots.

| Column | Calculation |
|--------|-------------|
| `rev_30d` | `eps_estimate_fwd[today] − eps_estimate_fwd[30d ago]` |
| `rev_60d` | `eps_estimate_fwd[today] − eps_estimate_fwd[60d ago]` |
| `rev_90d` | `eps_estimate_fwd[today] − eps_estimate_fwd[90d ago]` |

**Degenerate mode**: when `analyst_estimates` contains fewer than 30 days of history for a ticker, all three sub-factors default to 50.0 (sector median). Equal-weight the available non-degenerate deltas: if only 30d and 60d data exist, average those two.

A warning is logged if > 50% of the universe is still degenerate.

---

### 4.7 Short Interest (`factors/short_interest.py`) — 3 sub-factors

Uses the most recent snapshot from `short_interest` and the snapshot from ~30 days ago for the change calculation.

| Column | Calculation | Direction for LONGS | Direction for SHORTS |
|--------|-------------|---------------------|----------------------|
| `si_pct_float` | `short_pct_float` from latest snapshot | Lower = higher rank | Higher = higher rank |
| `si_days_to_cover` | `short_ratio` from latest snapshot | Lower = higher rank | Higher = higher rank |
| `si_change` | `(short_pct_float[latest] − short_pct_float[30d ago]) / short_pct_float[30d ago]` | Decline = higher rank | Increase = higher rank |

**Direction handling**: the short interest factor produces two versions — one scored for LONG suitability (declining short interest is good) and one for SHORT suitability (rising short interest is good). `composite.py` selects the appropriate version per direction. The stored sub-factor columns always use the LONG convention (lower short interest = higher score). `composite.py` flips the short interest factor score (100 − score) when computing the SHORT composite.

---

### 4.8 Insider Activity (`factors/insider.py`) — 2 scored sub-factors

**Pre-processing rule (transaction filter)**: only transaction codes `P` (open-market purchase) and `S` (sale) are counted. Codes `A`, `M`, `F` (grants, exercises) are excluded. The `is_open_market` flag from Layer 1 already encodes this; filter to `is_open_market = 1`.

| Column | Calculation |
|--------|-------------|
| `ins_net_flow` | Net dollar flow over 90 days. Each transaction: `shares × price` (positive for P, negative for S). CEO/CFO transactions (where `is_ceo_cfo = 1`) are weighted 3×. Sum per ticker |
| `ins_cluster_flag` | 1 if an `insider_cluster_flags` row exists within the 90-day window for this ticker, else 0. This binary is ranked within sector (most tickers = 0; any cluster flag = top of sector) |

When a ticker has no insider transactions in the 90-day window, both sub-factors default to 50.0 (sector median).

---

### 4.9 Institutional Flow (`factors/institutional.py`) — 3 sub-factors

Uses `institutional_summary` — the latest two report dates per ticker.

| Column | Calculation |
|--------|-------------|
| `inst_funds_holding` | `funds_holding` from the most recent report date |
| `inst_net_share_change` | `net_share_change` from the most recent report date (aggregate net buy/sell vs prior quarter) |
| `inst_simultaneous_open` | 1 if `new_positions >= 3` in the most recent report date, else 0 (same binary treatment as `ins_cluster_flag`) |

---

## 5. Scoring Methodology

### 5.1 Sector percentile ranking

All scoring follows this pipeline, applied independently for each factor and sub-factor:

```
raw value (float) 
  → within-GICS-sector percentile rank
  → scaled 0–100
```

Implementation using `pandas`:
```python
df['score'] = df.groupby('sector')['raw'].rank(pct=True, na_option='keep') * 100
```

**Minimum sector size**: if a sector has fewer than `min_sector_size` (default 5) tickers with non-NaN values for a sub-factor, fall back to universe-wide rank for that sub-factor. Log a warning.

**NaN handling**: tickers with `NaN` raw values receive the sector median score (50.0) after ranking. This is the correct behaviour for missing data (no information = neutral signal).

### 5.2 Sub-factor aggregation to factor score

Each factor score = **equal-weighted mean** of its sub-factor scores (after each sub-factor has been sector-ranked 0–100). The equal-weighted mean is then **re-ranked within sector** to produce the final factor score.

```python
factor_raw = sub_factor_df.mean(axis=1)          # equal-weight
factor_score = sector_rank(factor_raw, universe)  # re-rank
```

### 5.3 Composite score

Composite = weighted sum of the 8 factor scores, using weights from `config.yaml` (see §3.2). After blending, the composite is **re-ranked within sector** to produce the final 0–100 composite.

```python
composite_raw = sum(weight[f] * factor_scores[f] for f in factors)
composite_score = sector_rank(composite_raw, universe)
```

Weights must sum to 1.0. The entry point validates this at startup.

### 5.4 LONG / SHORT labelling

| Condition | Label |
|-----------|-------|
| `composite_score >= long_quintile_threshold` (default 80) | `LONG` |
| `composite_score <= short_quintile_threshold` (default 20) | `SHORT` |
| Otherwise | `NEUTRAL` |

---

## 6. Regime-Conditional Weights (`factors/regime_weights.py`)

When `scoring.regime_conditional_weights: true` in config, the composite weights are adjusted based on the most recent ^VIX close:

| Regime | Condition | Applied weights |
|--------|-----------|-----------------|
| `LOW_VOL` | VIX < 15 | momentum → 0.28, value → 0.10; other factors unchanged, re-normalised to sum to 1.0 |
| `NORMAL` | 15 ≤ VIX ≤ 25 | Default weights from config |
| `HIGH_VOL` | VIX > 25 | quality → 0.28, value → 0.22, momentum → 0.10; re-normalised |

The resolved regime and VIX value are written to the `regime_state` table.

If ^VIX data is unavailable (no recent row in `daily_prices`), log a warning and use `NORMAL` weights.

---

## 7. Crowding Detection (`factors/crowding.py`)

Crowding detection runs after scoring and requires at least 60 calendar days of historical `factor_scores` rows in the database.

**Algorithm**:
1. Load `factor_scores` for the past 60 calendar days.
2. For each day and each factor, identify the top-quintile tickers (score ≥ 80) and bottom-quintile tickers (score ≤ 20).
3. Compute the factor's daily return: mean return of top-quintile tickers minus mean return of bottom-quintile tickers (using `daily_prices` adj_close).
4. Compute the 60-day rolling pairwise Pearson correlation between all factor return series.
5. Compare each correlation to its academic baseline:
   - momentum vs value: baseline −0.30
   - momentum vs quality: baseline +0.10
   - all other pairs: no baseline (flag based on absolute magnitude > 0.6)
6. Flag any pair where `abs(rolling_corr − baseline) > 0.40`.
7. Write results to `crowding_flags` table.

**First-run behaviour**: if fewer than 60 days of `factor_scores` exist, skip crowding detection and log an informational message.

---

## 8. Entry Point (`run_scoring.py`)

```
python run_scoring.py [--ticker AAPL] [--date 2026-04-01] [--no-crowding]
```

| Flag | Behaviour |
|------|-----------|
| *(no flags)* | Full universe, today's date |
| `--ticker AAPL` | Single stock mode: score only AAPL (for fast debugging) |
| `--date YYYY-MM-DD` | Override score date (back-dated runs) |
| `--no-crowding` | Skip crowding detection |

**Execution sequence**:
```
1. Load config + resolve DB URL
2. Load scoring data from Layer 1 (loader.py)
3. Compute all 8 factor scores in order
4. Resolve regime → adjust weights (regime_weights.py)
5. Compute composite (composite.py)
6. Label LONG / SHORT
7. Write to factor_scores table (upsert)
8. Write regime_state (upsert)
9. Crowding detection (crowding.py) [unless --no-crowding]
10. Write crowding_flags (upsert)
11. Write scored_universe_latest.csv to output/
12. Print summary
```

**Summary output** (printed and logged):

```
=== Layer 2 Scoring Complete ===
Score date       : 2026-05-05
Regime           : NORMAL (VIX 18.4)
Universe scored  : 503 tickers across 11 sectors
LONG candidates  : 101
SHORT candidates : 100
Degenerate factors (revisions): 0 / 503 tickers
Crowding flags   : 0

Top 5 LONG candidates:
  NVDA   Composite: 97  Mom:95  Qual:89  Val:61  ...
  ...

Top 5 SHORT candidates:
  ...

Crowding warnings: None
```

---

## 9. Output CSV (`output/scored_universe_latest.csv`)

Columns (all sub-factor scores, factor scores, composite, labels):

```
ticker, sector, score_date, regime,
mom_12_1, mom_6m, mom_3m, mom_accel, mom_52w_high, mom_rel_strength, momentum_score,
val_fwd_earn_yield, val_book_to_price, val_fcf_yield, val_ev_ebitda_inv, val_shareholder_yield, val_sales_to_ev, value_score,
qual_roe_stability, qual_gm_level, qual_gm_trend, qual_de_inv, qual_cfo_to_ni, qual_accruals_inv, qual_piotroski, qual_altman_z, quality_score,
grw_rev_yoy, grw_earn_yoy, grw_rev_accel, grw_rd_intensity, grw_fcf_yoy, growth_score,
rev_30d, rev_60d, rev_90d, revisions_score,
si_pct_float, si_days_to_cover, si_change, short_interest_score,
ins_net_flow, ins_cluster_flag, insider_score,
inst_funds_holding, inst_net_share_change, inst_simultaneous_open, institutional_score,
composite_score, direction
```

---

## 10. Testing Plan

Tests live in `tests/` alongside the Layer 1 tests. The existing `tmp_db` fixture (SQLite-backed) is extended: `conftest.py` is updated so that `initialise_schema` triggers Layer 2 table creation automatically (via the shared `metadata` in `data.db`).

### Test files

| File | Test count (target) | Subject |
|------|---------------------|---------|
| `test_loader.py` | ~12 | Data loading functions, date filtering, empty-table handling |
| `test_momentum.py` | ~10 | Return calculations, 52w-high proximity, relative strength vs ETF, insufficient history |
| `test_value.py` | ~12 | All 6 sub-factor formulae, zero/negative EV handling, missing estimates |
| `test_quality.py` | ~15 | Piotroski all 9 signals individually, Altman Z-Score, ROE stability stdev, accruals |
| `test_growth.py` | ~10 | YoY calculations with missing prior periods, acceleration, R&D intensity |
| `test_revisions.py` | ~8 | Degenerate mode (< 30d), partial data (30d only), full 3-window |
| `test_short_interest.py` | ~8 | LONG/SHORT direction flip, missing prior snapshot, days-to-cover |
| `test_insider.py` | ~10 | CEO/CFO 3× weight, cluster flag bonus, no-data default, code filter |
| `test_institutional.py` | ~8 | Simultaneous open flag, missing prior quarter, single-fund case |
| `test_composite.py` | ~10 | Weight validation, LONG/SHORT labelling, regime weight application |
| `test_regime_weights.py` | ~6 | VIX threshold boundaries, re-normalisation, missing VIX fallback |
| `test_crowding.py` | ~8 | Insufficient history skip, flag trigger at deviation > 0.40, no-flag case |
| `test_scoring_db.py` | ~8 | Table creation, upsert idempotency, index presence |

**Target total:** ~125 tests

### Testing conventions

- All tests use the existing `tmp_db` (SQLite) fixture — no PostgreSQL required.
- Synthetic data is inserted directly into Layer 1 tables before calling factor functions.
- Factor functions accept a `pd.DataFrame` (not a DB connection) so they are trivially testable without DB setup.
- Each factor module exposes a `compute(data: dict[str, pd.DataFrame], config: dict) -> pd.DataFrame` function that returns a DataFrame with sub-factor columns. The DB write is handled separately in `composite.py` / `run_scoring.py`.

---

## 11. Key Dependencies

No new PyPI packages are required. All dependencies are already in `requirements.txt`:

- `pandas` — DataFrames, groupby ranking, rolling correlations
- `sqlalchemy` — DB reads and upserts (same helpers as Layer 1)
- `psycopg2-binary` — PostgreSQL adapter
- `numpy` — std dev, correlation matrix (available as pandas dependency)
- `pyyaml`, `python-dotenv` — config and secrets

---

## 12. Implementation Order

Build in this sequence to allow incremental testing:

1. `factors/db.py` — schema + `initialise_schema` integration
2. `factors/loader.py` + `test_loader.py`
3. One factor at a time (momentum → value → quality → growth → revisions → short_interest → insider → institutional), each with its test file
4. `factors/composite.py` + `test_composite.py`
5. `factors/regime_weights.py` + `test_regime_weights.py`
6. `factors/crowding.py` + `test_crowding.py`
7. `run_scoring.py` (integration, no dedicated test file needed — covered by above)
8. Update `config.yaml` with `scoring:` block
9. Update `docs/architecture.md` with Layer 2 section

---

## 13. Open Questions / Decisions

| Question | Recommendation |
|----------|---------------|
| Should sub-factor *raw* values be stored alongside percentile scores? | No — raw values can always be recomputed from Layer 1. Storing only percentile scores keeps the table manageable. |
| What happens on the first run with no prior `factor_scores` history (crowding)? | Skip crowding detection, log info. No error. |
| How to handle a ticker that appears in `sp500_universe` but has no price data? | Exclude from scoring, log warning. Do not write a row to `factor_scores`. |
| Should `run_scoring.py` auto-trigger `run_data.py` first? | No — they remain independent entry points. The user controls the pipeline order. |
| Should the Short Interest factor store LONG-convention scores or both? | Store LONG-convention (lower SI = higher score). `composite.py` applies `100 − score` for the SHORT composite calculation inline. |
