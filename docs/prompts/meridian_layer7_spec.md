# Meridian Capital Partners — Layer 7: Reporting & Dashboard

Build Layer 7 of the Meridian Capital Partners hedge fund. Layers 1–6 are built.  
Build **BOTH** the reporting engine **AND** the Streamlit dashboard with **JARVIS persona.**

---

## Reporting Engine

### 1. Daily P&L Attribution (`reporting/pnl_attribution.py`)

Decompose daily return into four components:

| Component | Method |
|-----------|--------|
| Beta | `net_beta * SPY_return` |
| Sector | Brinson-style |
| Factor | Regression on factor return spreads |
| Alpha residual | Residual after subtracting all three |

Persist to `output/daily_attribution.csv`

---

### 2. Position Attribution

- Mark-to-market, FIFO round-trips, best/worst per side
- Predictive power: Spearman correlation between entry-time score and realized return

---

### 3. Win/Loss Analysis

- Win rate, P/L ratio
- Sliced by: side, holding period (1–5d / 5–20d / 20–60d / 60d+), sector, VIX regime at entry, factor quintile at entry
- Streaks

---

### 4. Sector-Relative Performance

- Per sector, 90d: your picks vs sector ETF = stock-selection alpha
- Sum across sectors = total alpha
- Track winner/loser sector counts

---

### 5. Turnover Analytics

- Trailing 30/90d turnover, annualized, vs budget from config
- Tax estimate via FIFO: short-term gains @ 37%, long-term @ 20%

---

### 6. Tear Sheet

Markdown institutional format including:
- Metrics vs SPY
- Monthly returns grid
- Equity curve
- Drawdown
- Rolling 12mo Sharpe
- Factor + sector exposures
- Turnover

---

### 7. Claude Weekly Commentary

- JARVIS-authored
- Fires on configurable weekday (default: Friday)

---

### 8. Daily LP Letter

- 3–4 paragraphs
- Letterhead, signature block, compliance footer

---

## Streamlit Dashboard

**Served at:** `http://localhost:8502`

### Visual Design

| Element | Value |
|---------|-------|
| Background | `#0b0e17` |
| Cards gradient | `#131827` → `#1a2035` |
| Accent indigo | `#6366f1` |
| Long colour | `#10b981` |
| Short colour | `#f43f5e` |
| Fonts | Plus Jakarta Sans + JetBrains Mono |

- Hide all Streamlit chrome
- Nav: Roman-numeral pill bar — **I PORTFOLIO · II RESEARCH · III RISK · IV PERFORMANCE · V EXECUTION · VI LETTER**
- Active page = indigo gradient

---

### Page I — Portfolio (Cover)

- Right 56%: robot image or dark gradient fallback
- Left: "JARVIS" 92px, "LONG/SHORT HEDGE FUND ANALYST" 11px small caps
- Ask JARVIS chat (input + response, preserve 6 turns)
- 10 metrics: Universe, Long/Short Candidates, Positions, Crowding, Insider Events, CEO Buys, Cluster Buys, VIX, Earnings 7d
- Status strip: VIX regime badge + data source indicator
- JARVIS: build ~19KB JSON snapshot of system state, send as cached context to Claude

---

### Page II — Research

- KPIs, crowding warnings, rebalance advisory banner (earnings/FOMC/opex)
- Optimization toggle (MVO/conviction radio)
- Factor heatmap (top 30 + bottom 30 × 8 factors)
- Approval banner with Execute button
- 10 long + 10 short candidate cards with Piotroski/Altman scores
- Approve/Reject/Reset buttons, expandable Claude analysis per ticker
- Execute → pre-trade veto (8 checks) → Alpaca
- Rejected trades show veto reason

---

### Page III — Risk

- Circuit breaker bars (daily/weekly/drawdown)
- Tail-risk KPIs (VIX + credit spread)
- Risk decomposition donut (factor vs specific variance)
- Factor risk contributions table
- MCTR table with disproportionate-risk flag
- Factor exposure bars with 1.5-sigma warnings
- Stress test table (6 scenarios)
- Correlation heatmap + effective bets
- 72hr alerts

---

### Page IV — Performance

- Equity curve vs SPY (rebased to 100)
- Monthly returns grid (green/red heatmap)
- Drawdown chart
- P&L attribution bars (Beta / Sector / Factor / Alpha)
- Rolling 12mo Sharpe
- Sector-relative alpha chart with total alpha KPI + winner/loser counts
- Turnover panel (30d / annualized / budget / tax)
- Transaction cost panel (estimated vs actual vs model error)
- Best/worst 5 contributors
- Win/loss panel
- Claude weekly commentary card

---

### Page V — Execution

- KPI row: filled orders 30d, avg slippage bps, total slippage $, open orders count
- Open orders table (polling Alpaca)
- Recent trades log (last 200 orders)
- Worst 5 fills
- Short availability panel per current short
- Daily notional turnover table

---

### Page VI — Letter

Formal daily LP letter:
- **Letterhead:** fund name, domicile (Delaware), inception, AUM, doc ID (`MCP-IM-{YYYY}-{MMDD}`), date
- `"CONFIDENTIAL · LIMITED PARTNERS ONLY"` stamp
- `"Dear Limited Partners,"` + 3–4 paragraph body from Claude in JARVIS voice
- Signature block + compliance footer
- `"Regenerate letter"` button
- Cache by date

---

## Auto-Refresh

**Every 5 minutes during market hours (9:30am – 4:00pm ET)**

---

## Daily Automation

macOS launchd plist at `~/Library/LaunchAgents/com.user.hedgefund.daily.plist`

- Weekdays at **17:15 local**
- Runs: `run_scoring.py --no-filings --no-13f`
- Refreshes: prices, short interest, estimates, calendar, rescores all factors
- Duration: ~10 min
