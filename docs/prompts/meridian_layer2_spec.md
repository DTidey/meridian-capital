# Meridian Capital Partners — Layer 2: Scoring Engine

Build Layer 2 of the Meridian Capital Partners hedge fund. Layer 1 (data) is built.  
Build the scoring engine: **8 factors with 27 sub-factors.**  
All scores are **0–100 percentile rank WITHIN each GICS sector.**

---

## Factors

### 1. Momentum (`factors/momentum.py`) — 6 sub-factors

| Sub-factor | Description |
|------------|-------------|
| 12-1 month return | Skip recent 1mo to avoid reversal |
| 6-month return | — |
| 3-month return | — |
| Acceleration | Recent 3m minus older 3m |
| 52-week-high proximity | Price / 52w high (George & Hwang 2004) |
| Relative strength vs sector ETF | 6m stock return minus sector ETF return — isolates stock-specific momentum from sector beta |

---

### 2. Value (`factors/value.py`) — 6 sub-factors

| Sub-factor | Description |
|------------|-------------|
| Forward earnings yield | 1 / forward P/E |
| Book-to-price | — |
| FCF yield | — |
| EV/EBITDA | Inverted |
| Shareholder yield | TTM buybacks + dividends / mkt cap |
| Sales-to-EV | Revenue / EV — works where P/E breaks on negative or volatile earnings |

---

### 3. Quality (`factors/quality.py`) — 8 sub-factors

| Sub-factor | Description |
|------------|-------------|
| ROE stability | Std dev of 12Q ROEs, inverted |
| Gross margin level | — |
| Gross margin trend | Latest minus 4Q ago |
| Debt/equity | Inverted |
| CFO/NI | Higher = real cash earnings |
| Accruals ratio | (NI-CFO)/TA, inverted — high accruals predict underperformance |
| Piotroski F-Score (1–9) | 9 binary signals: positive ROA, positive CFO, rising ROA, CFO > NI, falling D/E, rising current ratio, no dilution, rising gross margin, rising asset turnover. Color code: green ≥7, amber ≤3 |
| Altman Z-Score | `1.2*(WC/TA) + 1.4*(RE/TA) + 3.3*(EBIT/TA) + 0.6*(MktCap/TL) + 1.0*(Sales/TA)`. >2.99 = "safe" (green), 1.81–2.99 = "grey zone", <1.81 = "distress" (amber) |

---

### 4. Growth (`factors/growth.py`) — 5 sub-factors

| Sub-factor | Description |
|------------|-------------|
| Revenue growth YoY | — |
| Earnings growth YoY | — |
| Revenue growth acceleration | Latest YoY minus 4Q-ago YoY |
| R&D intensity | R&D expense / revenue — high R&D in tech/healthcare tends to outperform long-term |
| Free cash flow growth YoY | Harder to manipulate than earnings |

---

### 5. Estimate Revisions (`factors/revisions.py`) — 3 sub-factors

| Sub-factor | Description |
|------------|-------------|
| 30-day change in consensus next-Q EPS | — |
| 60-day change | — |
| 90-day change | — |

> Degenerate (all scores = 50) until ~30 days of snapshots accumulate. Equal-weight available deltas.

---

### 6. Short Interest (`factors/short_interest.py`) — 3 sub-factors

| Sub-factor | Description |
|------------|-------------|
| Short percent of float | — |
| Days to cover | — |
| Change in short interest vs prior period | — |

> For **LONGS**: declining short interest scores higher. For **SHORTS**: increasing scores higher.

---

### 7. Insider Activity (`factors/insider.py`) — 3 sub-factors

| Sub-factor | Description |
|------------|-------------|
| Net dollar flow over 90 days | From Form 4 data. CEO/CFO open-market purchases weighted 3x vs other insiders |
| Cluster-buy flag | 3+ insiders within 30 days = bonus |
| Transaction filter | Only count codes P (purchase) and S (sale); ignore A/M/F. No data = sector median (50) |

---

### 8. Institutional Flow (`factors/institutional.py`) — 3 sub-factors

| Sub-factor | Description |
|------------|-------------|
| Number of tracked funds holding | — |
| Net change in aggregate holdings vs prior quarter | — |
| Multi-fund simultaneous opening flag | 3+ funds opening new positions on same ticker |

---

## Scoring Methodology

> **All factors:** equal-weight sub-factors within each parent, then sector percentile rank 0–100.

---

## Composite + Extras

### 9. Composite Score (`factors/composite.py`)

Weighted blend of all factors:

| Factor | Weight |
|--------|--------|
| Momentum | 0.20 |
| Quality | 0.20 |
| Value | 0.15 |
| Estimate Revisions | 0.15 |
| Insider Activity | 0.10 |
| Growth | 0.10 |
| Short Interest | 0.05 |
| Institutional Flow | 0.05 |

- After blending, re-rank within sector for final 0–100 composite
- Top quintile = **LONG** candidates. Bottom quintile = **SHORT** candidates
- Output: `scored_universe_latest.csv` with ALL sub-factor scores, composite, and LONG/SHORT flag

---

### 10. Regime-Conditional Weights (`factors/regime_weights.py`)

| Regime | Condition | Adjustment |
|--------|-----------|------------|
| Low Vol | VIX < 15 | Boost momentum 0.20→0.28, cut value 0.15→0.10 |
| Normal | VIX 15–25 | Default weights |
| High Vol | VIX > 25 | Boost quality 0.20→0.28 and value 0.15→0.22, cut momentum 0.20→0.10 |

Config flag: `regime_conditional_weights`

---

### 11. Crowding Detection (`factors/crowding.py`)

- Synthesize daily factor returns: top-quintile minus bottom-quintile per factor, 60-day rolling
- Pairwise correlations between all factor return series
- Compare to academic baselines: momentum/value ~−0.3, momentum/quality ~+0.1
- Flag when deviation > 0.4

---

## Entry Point

**`run_scoring.py`** — use `--ticker AAPL` for single stock mode.

Print summary: top 5 longs, top 5 shorts, crowding warnings, degenerate factor warnings.
