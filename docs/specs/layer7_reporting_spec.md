# Layer 7 — Reporting & Dashboard

**Status:** Complete  
**Depends on:** Layers 1–6 (all tables in `data.db.metadata`)  
**Entry points:** `run_reporting.py`, `dashboard/app.py`  
**Served at:** `http://localhost:8502`

---

## Overview

Layer 7 has two independently runnable parts:

| Part | Purpose | Command |
|---|---|---|
| Reporting engine | Nightly P&L decomposition, tear sheet, LP letter, commentary | `python run_reporting.py` |
| Streamlit dashboard | Live 6-page JARVIS UI | `streamlit run dashboard/app.py --server.port 8502` |

Both parts are read-only with respect to Layers 1–6; they never write to tables owned by earlier layers.

---

## Design Decisions

| Decision | Choice | Reason |
|---|---|---|
| Database | Shared PostgreSQL via `data.db.get_engine` | Consistent with all other layers |
| AI model | `gpt-4o` via existing `openai.OpenAI` client | JARVIS persona; reuses `OPENAI_API_KEY` already in env |
| SPY benchmark | Pulled from `daily_prices` (ticker='SPY') | Already ingested by Layer 1 |
| FIFO implementation | In-memory over `portfolio_history` snapshots | No separate trade ledger exists |
| Auto-refresh | `st.rerun()` + `time.sleep(300)` in a thread | Market-hours gate (9:30–16:00 ET) |
| LP letter cache | `lp_letters` table keyed by date | Regenerate button writes a new row |
| Commentary cache | `weekly_commentary` table keyed by week_start | Fired on configurable weekday |
| Tax estimate | FIFO with short-term @ 37%, long-term @ 20% | Holding period threshold: 365 days |
| Factor regression | OLS of portfolio daily return on 8 factor-return spreads | scipy.stats.linregress; daily from `factor_scores` |
| Sector ETF returns | From `daily_prices` via `config.yaml` `sector_etf_map` | Same ETFs already tracked |

---

## Database — `reporting/db.py`

All tables registered on `data.db.metadata`.

### `pnl_attribution`

| Column | Type | Notes |
|---|---|---|
| date | String PK | ISO date |
| portfolio_return | Float | Total daily return (%) |
| spy_return | Float | SPY daily return (%) |
| beta_pnl | Float | `net_beta × spy_return` |
| sector_pnl | Float | Brinson allocation + selection |
| factor_pnl | Float | OLS residual attributable to factor spreads |
| alpha_pnl | Float | `portfolio_return − beta_pnl − sector_pnl − factor_pnl` |
| net_beta | Float | Portfolio net beta at day open |
| computed_at | String | ISO timestamp |

### `portfolio_nav`

| Column | Type | Notes |
|---|---|---|
| date | String PK | ISO date |
| nav | Float | End-of-day NAV in USD |
| spy_close | Float | SPY closing price |
| drawdown_pct | Float | Max drawdown from peak NAV |
| computed_at | String | ISO timestamp |

### `position_trades`

FIFO-matched round trips derived from `portfolio_history`.

| Column | Type | Notes |
|---|---|---|
| id | Integer PK autoincrement | |
| ticker | String | |
| direction | String | LONG / SHORT |
| entry_date | String | |
| exit_date | String | Null if still open |
| entry_price | Float | |
| exit_price | Float | Null if still open |
| shares | Float | |
| realized_pnl | Float | Null if still open |
| holding_days | Integer | Null if still open |
| sector | String | From `sp500_universe` |
| entry_score | Float | `combined_scores.combined_score` at entry_date |
| entry_vix | Float | `daily_prices` `^VIX` at entry_date |

### `lp_letters`

| Column | Type | Notes |
|---|---|---|
| letter_date | String PK | ISO date |
| doc_id | String | `MCP-IM-{YYYY}-{MMDD}` |
| content | Text | Full markdown body (paragraphs only, no letterhead) |
| generated_at | String | ISO timestamp |

### `weekly_commentary`

| Column | Type | Notes |
|---|---|---|
| week_start | String PK | ISO date of Monday |
| content | Text | JARVIS markdown commentary |
| generated_at | String | ISO timestamp |

---

## Module Structure

```
ls_equity_fund/
  reporting/
    __init__.py
    db.py                  # Five tables above
    nav_series.py          # Build daily NAV from portfolio_history; persist portfolio_nav
    pnl_attribution.py     # Daily return decomposition; persist pnl_attribution
    position_attribution.py# FIFO round-trips; persist position_trades
    win_loss.py            # Win/loss analysis; returns DataFrames, no new DB table
    sector_performance.py  # Sector-relative alpha; returns DataFrames
    turnover.py            # 30/90d turnover + tax estimate; returns dict
    tear_sheet.py          # Markdown institutional tear sheet; writes output/tear_sheet.md
    commentary.py          # JARVIS weekly commentary; persist weekly_commentary
    lp_letter.py           # JARVIS daily LP letter; persist lp_letters
  dashboard/
    __init__.py
    app.py                 # Streamlit entry point; nav + page routing + auto-refresh
    theme.py               # CSS injection, design tokens, card/metric helpers
    jarvis.py              # Snapshot builder + Claude chat interface
    page_portfolio.py      # Page I
    page_research.py       # Page II
    page_risk.py           # Page III
    page_performance.py    # Page IV
    page_execution.py      # Page V
    page_letter.py         # Page VI
  run_reporting.py         # CLI entry point for reporting engine
```

---

## Reporting Engine

### `run_reporting.py`

```
usage: python run_reporting.py [--date YYYY-MM-DD] [--commentary] [--letter] [--tearsheet] [--all]
```

Default (no flags): runs nav_series + pnl_attribution + position_attribution only.  
`--commentary`: also generate JARVIS weekly commentary (respects weekday config).  
`--letter`: also generate daily LP letter.  
`--tearsheet`: also write tear sheet markdown.  
`--all`: all of the above.

Execution order (sequential, shared engine):

1. `nav_series.build_nav_series(engine)` → populates `portfolio_nav`
2. `pnl_attribution.run(engine)` → populates `pnl_attribution`
3. `position_attribution.build_trades(engine)` → populates `position_trades`
4. *(if `--tearsheet` or `--all`)* `tear_sheet.write(engine)` → `output/tear_sheet.md`
5. *(if `--commentary` or `--all`)* `commentary.generate_if_due(engine, cfg)`
6. *(if `--letter` or `--all`)* `lp_letter.generate(engine, cfg)`

All steps are idempotent (upsert on PK; tear sheet overwrites file).

---

### `reporting/nav_series.py`

**Purpose:** compute daily NAV and max-drawdown series from `portfolio_history`.

```python
def build_nav_series(engine) -> pd.DataFrame:
    """
    For each distinct snapshot_date in portfolio_history:
      nav = config.portfolio.nav_usd + sum(unrealized_pnl)
    Compute daily returns and rolling max-drawdown.
    Upsert into portfolio_nav.
    Returns DataFrame(date, nav, spy_close, drawdown_pct).
    """
```

SPY close comes from `daily_prices` where `ticker='SPY'`.

---

### `reporting/pnl_attribution.py`

**Purpose:** decompose each day's portfolio return into four additive components.

```python
def run(engine) -> pd.DataFrame:
    """
    For each date in portfolio_nav where pnl_attribution row doesn't exist yet:
      1. beta_pnl  = net_beta_at_open * spy_return
      2. sector_pnl = Brinson (allocation + selection) across GICS sectors
         - sector weights from portfolio_history at date open
         - sector returns from daily_prices for sector ETFs
      3. factor_pnl = OLS(portfolio_return ~ factor_return_spreads)[fitted] − beta_pnl
         - factor spreads: for each of 8 factors, long-quintile mean return − short-quintile mean return
           derived from factor_scores composite sub-factor scores × daily_prices returns
      4. alpha_pnl = portfolio_return − beta_pnl − sector_pnl − factor_pnl
    Upsert into pnl_attribution.
    Also writes output/daily_attribution.csv (full history, appended idempotently).
    """
```

Net beta at open: sum of `portfolio_positions.beta * weight` at that date's `portfolio_history` snapshot.

Factor return spreads method:
- Group tickers by factor quintile on each `score_date`
- Q5 mean daily return − Q1 mean daily return = factor spread for that day
- Run `scipy.stats.linregress` (or `numpy.linalg.lstsq`) over 60-day rolling window
- `factor_pnl` = dot(beta_coefficients, factor_spreads_today)

Brinson sector method:
- For each sector `s`: allocation effect = `(w_s − bm_w_s) * (bm_s_ret − bm_ret)`; selection effect = `w_s * (port_s_ret − bm_s_ret)`
- Benchmark weights = equal-weighted across sector ETFs
- `sector_pnl` = sum of allocation + selection effects

---

### `reporting/position_attribution.py`

**Purpose:** build FIFO round-trips from `portfolio_history` snapshots and score predictive power.

```python
def build_trades(engine) -> pd.DataFrame:
    """
    Algorithm:
      - Sort portfolio_history by (ticker, snapshot_date)
      - Entry: first date a ticker appears (or re-appears after exit)
      - Exit: last date before ticker disappears from history OR direction flips
      - FIFO: if shares decrease, match oldest lot first
    Persist to position_trades (upsert on id via date+ticker+entry_date composite).
    Returns DataFrame of closed trades only.
    """

def spearman_predictive_power(engine) -> dict:
    """
    Join position_trades (closed) with combined_scores at entry_date.
    Return: {'spearman_r': float, 'p_value': float, 'n': int}
    Separately for LONG and SHORT sides.
    """
```

---

### `reporting/win_loss.py`

**Purpose:** compute win/loss statistics across multiple slice dimensions. No DB persistence (computed fresh each call from `position_trades`).

```python
def compute(engine) -> dict:
    """
    Returns dict with keys:
      overall:  {win_rate, pl_ratio, avg_win, avg_loss, total_trades}
      by_side:  {LONG: {...}, SHORT: {...}}
      by_holding_period: {'1-5d': {...}, '5-20d': {...}, '20-60d': {...}, '60d+': {...}}
      by_sector: {sector_name: {...}}
      by_vix_regime: {'low (<15)': {...}, 'mid (15-25)': {...}, 'high (>25)': {...}}
      by_factor_quintile: {1: {...}, 2: {...}, 3: {...}, 4: {...}, 5: {...}}
      streaks: {longest_win_streak: int, longest_loss_streak: int, current_streak: str}
    """
```

Win = `realized_pnl > 0`. P/L ratio = `mean(winning trades) / abs(mean(losing trades))`.  
VIX regime at entry: look up `entry_vix` from `position_trades.entry_vix`.  
Factor quintile at entry: `entry_score` bucketed into quintiles over all tickers that date.

---

### `reporting/sector_performance.py`

**Purpose:** compute 90-day stock-selection alpha per sector.

```python
def compute(engine, lookback_days: int = 90) -> pd.DataFrame:
    """
    For each sector:
      - portfolio sector return = weighted avg return of held stocks (from portfolio_history + daily_prices)
      - benchmark sector return = sector ETF return (from daily_prices)
      - alpha = portfolio_sector_return − benchmark_sector_return
    Aggregated over lookback_days.
    Returns DataFrame(sector, portfolio_return, etf_return, alpha, num_longs, num_shorts).
    Also returns: total_alpha (sum), winner_count, loser_count.
    """
```

---

### `reporting/turnover.py`

**Purpose:** compute trailing turnover and tax estimate.

```python
def compute(engine) -> dict:
    """
    Returns:
      turnover_30d_pct:   float   # (buys+sells) / 2 / avg_nav over 30d
      turnover_90d_pct:   float
      turnover_annualized: float  # 30d annualized × 12
      budget_pct:         float   # from config.portfolio.turnover_budget_pct
      tax_estimate_usd:   float   # FIFO: short-term gains * 0.37 + long-term gains * 0.20
      short_term_gains:   float
      long_term_gains:    float
    """
```

Turnover denominator: average NAV over the period from `portfolio_nav`.  
Turnover numerator: sum of `|market_value_change|` per ticker per day from `portfolio_history`.

---

### `reporting/tear_sheet.py`

**Purpose:** write `output/tear_sheet.md` — institutional-format markdown.

Sections (in order):

1. **Header** — Fund name, inception date, AUM (latest NAV), report date
2. **Performance vs SPY** — annualized return, Sharpe, Sortino, max drawdown, beta, alpha (Jensen's), Calmar; side-by-side with SPY equivalents
3. **Monthly Returns Grid** — calendar table, rows=years, cols=months + annual; green/red shading via markdown bold
4. **Equity Curve** — ASCII sparkline (80 chars) using `▁▂▃▄▅▆▇█` characters; portfolio rebased to 100 vs SPY
5. **Drawdown** — peak date, trough date, recovery date (if recovered), max drawdown %
6. **Rolling 12-Month Sharpe** — last 12 data points as ASCII sparkline
7. **Factor Exposures** — table: factor name, current z-score, 30d avg; from `factor_scores` composite sub-scores
8. **Sector Exposures** — table: sector, long weight, short weight, net weight
9. **Turnover** — 30d, 90d, annualized, vs budget
10. **Recent Execution** — last 30d slippage stats (from `execution.costs.slippage_stats`)

```python
def write(engine, output_path: str = "output/tear_sheet.md") -> None: ...
```

Helper: `_sharpe(returns: pd.Series, rf: float = 0.05) -> float` using annualized mean / std.

---

### `reporting/commentary.py`

**Purpose:** generate JARVIS weekly commentary and persist to `weekly_commentary`.

```python
def generate_if_due(engine, cfg: dict) -> str | None:
    """
    Check if today is the configured weekday (default: Friday, configurable in config.yaml
    under reporting.commentary_weekday, 0=Monday).
    Check if this week's commentary already exists in weekly_commentary.
    If due and not cached: build context snapshot, call Claude, upsert.
    Returns commentary text or None if not due.
    """
```

OpenAI call:
- Model: `gpt-4o` (from `config.analysis.openai_model`)
- Client: `openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])` — same key used by Layer 3
- System: JARVIS persona (see §JARVIS Persona)
- User prompt: last 5 days of `pnl_attribution`, top 5 / bottom 5 contributors from `position_trades`, current `portfolio_nav`, VIX level, any `risk_events` from this week
- `json_mode=False` (prose output); call `response.choices[0].message.content` directly
- Max tokens: 800

---

### `reporting/lp_letter.py`

**Purpose:** generate daily LP letter and persist to `lp_letters`.

```python
def generate(engine, cfg: dict, force: bool = False) -> str:
    """
    If today's letter exists and not force: return cached content.
    Otherwise: build context, call Claude, upsert, return content.
    """

def render_full(letter_date: str, content: str) -> str:
    """
    Returns full rendered markdown including letterhead and footer.
    Letterhead: fund name, 'Wilmington, Delaware', inception date, AUM, doc_id, date.
    Stamp: 'CONFIDENTIAL · LIMITED PARTNERS ONLY'
    Body: 'Dear Limited Partners,' + content
    Signature: 'JARVIS\nPortfolio Intelligence System\nMeridian Capital Partners'
    Compliance footer: standard fund disclaimer (not investment advice, etc.)
    """
```

OpenAI call:
- Model: `gpt-4o` (from `config.analysis.openai_model`)
- Client: `openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])`
- System: JARVIS persona
- User prompt: today's `pnl_attribution`, current positions summary (long/short count, gross/net exposure), top 3 movers, VIX, any circuit breaker events today
- `json_mode=False`; `temperature=0.7` for natural prose; `max_tokens=600`

---

## JARVIS Persona

System prompt (shared across commentary, LP letter, and dashboard chat):

```
You are JARVIS — the portfolio intelligence system for Meridian Capital Partners,
a quantitative long/short equity hedge fund. You speak with authority, precision,
and a dry wit. You reference specific positions, factor scores, and risk metrics.
You never hedge with "I think" or "perhaps". You write like a seasoned PM who
happens to have read every 10-K and knows every basis point.
```

---

## Dashboard

### `dashboard/app.py`

Entry point. Responsibilities:
1. `st.set_page_config(layout="wide", page_title="JARVIS", page_icon="🤖")`
2. Inject global CSS from `theme.inject_css()`
3. Render nav pill bar; read `st.session_state.page` (default `"I"`)
4. Route to page module `render()` function
5. Auto-refresh thread: if market hours (09:30–16:00 ET, weekday), `st.rerun()` every 300 seconds

Auto-refresh implementation:
```python
import threading, time
from datetime import datetime
import pytz

def _refresh_loop():
    et = pytz.timezone("America/New_York")
    while True:
        time.sleep(300)
        now = datetime.now(et)
        if now.weekday() < 5 and (9, 30) <= (now.hour, now.minute) <= (16, 0):
            st.rerun()

if "refresh_thread" not in st.session_state:
    t = threading.Thread(target=_refresh_loop, daemon=True)
    t.start()
    st.session_state.refresh_thread = t
```

---

### `dashboard/theme.py`

Design tokens and CSS injection.

```python
DARK_BG      = "#0b0e17"
CARD_GRAD_A  = "#131827"
CARD_GRAD_B  = "#1a2035"
ACCENT       = "#6366f1"
LONG_COL     = "#10b981"
SHORT_COL    = "#f43f5e"
NEUTRAL      = "#94a3b8"
FONT_SANS    = "Plus Jakarta Sans"
FONT_MONO    = "JetBrains Mono"

def inject_css() -> None:
    """st.markdown with <style> block. Imports Google Fonts. Hides Streamlit chrome
    (header, footer, deploy button). Defines: .card, .metric-card, .pill-nav,
    .pill-active, .long-badge, .short-badge, .veto-reason."""

def card(content: str) -> None:
    """st.markdown with .card wrapper."""

def metric_card(label: str, value: str, delta: str = "", colour: str = NEUTRAL) -> None:
    """Single KPI tile rendered as .metric-card div."""

def section_header(title: str) -> None:
    """Small-caps indigo divider."""
```

---

### `dashboard/jarvis.py`

JARVIS chat and snapshot builder.

```python
SNAPSHOT_KEYS = [
    "nav_usd", "nav_change_1d", "gross_exposure", "net_exposure", "net_beta",
    "long_count", "short_count", "long_candidates", "short_candidates",
    "universe_size", "vix", "vix_regime",
    "crowding_flags",          # list of tickers with crowding warnings
    "insider_events_30d",      # count
    "ceo_buys_30d",            # count
    "cluster_buys_active",     # count of active cluster flags
    "earnings_7d",             # list of tickers with earnings in next 7 days
    "top5_longs",              # [{ticker, weight, score, sector, unrealized_pnl}]
    "top5_shorts",             # [{ticker, weight, score, sector, unrealized_pnl}]
    "pnl_today",               # {total, beta, sector, factor, alpha}
    "circuit_breaker_status",  # {daily_loss_pct, weekly_loss_pct, drawdown_pct}
    "halt_lock_active",        # bool
    "last_execution_date",     # ISO date
    "data_freshness",          # {"prices": ISO, "scores": ISO, "risk": ISO}
]

def build_snapshot(engine) -> dict:
    """
    Query all relevant tables and assemble ~19KB JSON snapshot.
    Cache in st.session_state with 60s TTL.
    """

def render_chat(engine) -> None:
    """
    Render 'Ask JARVIS' section:
    - st.chat_input
    - Keep last 6 turns in st.session_state.jarvis_history
    - On submit: prepend snapshot as cached system context; stream Claude response
    - Display conversation with st.chat_message
    """
```

OpenAI call for chat:
- Model: `gpt-4o` (from `config.analysis.openai_model`)
- Client: `openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])`
- System message: JARVIS persona + snapshot JSON (sent as single system message; OpenAI auto-caches repeated system prefixes)
- Messages: last 6 turns from `st.session_state.jarvis_history`
- Streaming: `client.chat.completions.create(stream=True)`; iterate `chunk.choices[0].delta.content` into `st.write_stream()`

---

### Page I — Portfolio (`dashboard/page_portfolio.py`)

```python
def render(engine) -> None:
```

Layout: two columns, `[0.44, 0.56]`.

**Left column (top to bottom):**

1. "JARVIS" in 92px weight-800; "LONG/SHORT HEDGE FUND ANALYST" 11px small-caps letterspaced
2. 10 KPI metric cards in a `st.columns(2)` grid:
   - Universe size (count from `sp500_universe`)
   - Long Candidates (direction='LONG' in latest `factor_scores`)
   - Short Candidates (direction='SHORT' in latest `factor_scores`)
   - Positions (count from `portfolio_positions`)
   - Crowding Flags (tickers in latest `factor_scores` with crowding deviation > threshold)
   - Insider Events 30d (count from `insider_transactions` last 30d)
   - CEO Buys 30d (count where `is_ceo_cfo=1` and `transaction_type='P'`)
   - Cluster Buys (active rows in `insider_cluster_flags`)
   - VIX (latest `daily_prices` where `ticker='^VIX'`)
   - Earnings 7d (count from `earnings_calendar` next 7 days)
3. Status strip (single row):
   - VIX regime badge: green "LOW VIX" / amber "CAUTION" / red "STRESS" per `config.risk.tail_risk`
   - Data source pill: "LIVE" if latest price is today, "DELAYED (Nd)" otherwise
4. JARVIS chat widget (`jarvis.render_chat(engine)`)

**Right column:**
- Dark gradient fill `CARD_GRAD_A` to `CARD_GRAD_B` (full column height via CSS)
- Centred robot SVG icon or base64 robot image if `assets/robot.png` exists

---

### Page II — Research (`dashboard/page_research.py`)

```python
def render(engine) -> None:
```

**Section 1 — KPIs**

Row of 5: Universe, Long/Short Candidates, Crowding Flags, Latest Score Date, Regime.

**Section 2 — Rebalance Advisory Banner**

Check three conditions, show amber warning banner if any true:
- Any held ticker has earnings within `config.portfolio.earnings_blackout_days` days
- FOMC meeting within 3 days (hardcoded list of known 2025–2026 dates in `FOMC_DATES` constant)
- Monthly OpEx Friday within 3 days (third Friday of month)

**Section 3 — Optimization Toggle**

`st.radio("Portfolio construction", ["MVO", "Conviction-weighted"])` — stores choice in `st.session_state.opt_mode`. Passed to execution pre-trade note only (does not re-run Layer 4).

**Section 4 — Factor Heatmap**

Top 30 longs + bottom 30 shorts × 8 factor scores.  
Source: latest `factor_scores` joined with `sp500_universe` for sector.  
Rendered as `st.dataframe` with pandas Styler background gradient per column.  
Columns: ticker, sector, momentum, value, quality, growth, revisions, insider, institutional, short_interest, composite.

**Section 5 — Approval Banner**

If any rows in `position_approvals` with status=PENDING:
- Yellow banner: "{N} trades pending approval"
- **Execute** button: on click → call `_execute_approved(engine)` which:
  1. Runs 8 pre-trade veto checks (see below)
  2. For each trade passing all checks: sets approval status to APPROVED (only if currently PENDING — no re-approving already reviewed rows)
  3. Triggers `run_execution.py` as subprocess with `--date TODAY`
  4. Shows spinner; on completion shows success toast

**Pre-trade veto checks (8):**

| # | Check | Source |
|---|---|---|
| 1 | Halt lock active | `cache/halt.lock` file exists |
| 2 | Market closed | Alpaca market clock or 09:30–16:00 ET gate |
| 3 | Daily loss circuit breaker | `risk_events` today with event_type containing 'CLOSE_ALL' or 'SIZE_DOWN' |
| 4 | Kill switch triggered | `risk_events` today with event_type='KILL_SWITCH' |
| 5 | Gross exposure would exceed `config.risk.pre_trade.max_gross` | Compute post-trade gross from `portfolio_positions` + pending trades |
| 6 | Net exposure out of bounds | Post-trade net vs `net_min`/`net_max` |
| 7 | Any ticker has earnings blackout | Check `earnings_calendar` vs `config.portfolio.earnings_blackout_days` |
| 8 | Short not available (HTB) | `execution.short_check.is_shortable()` for SHORT/COVER actions |

Failed checks: show per-trade veto reason in `.veto-reason` styled div; do not execute that trade.

**Section 6 — Candidate Cards (2 × 10)**

Two columns: Longs (green accent) | Shorts (red accent).  
Each card shows: ticker, company name, sector, composite score, Piotroski score, Altman-Z score, unrealized PnL if held.  
Expander: Claude `analysis_results` for this ticker (latest `result_json`), rendered as markdown.  
Approve / Reject / Reset buttons update `position_approvals.status` for this ticker's latest pending row.

---

### Page III — Risk (`dashboard/page_risk.py`)

```python
def render(engine) -> None:
```

All data sourced from existing Layer 5 tables + live computation.

**Section 1 — Circuit Breaker Bars**

Three horizontal progress bars (0–100%) with colour thresholds:
- Daily loss: `risk.circuit_breakers.daily_size_down` (amber) / `daily_close_all` (red)
- Weekly loss: `risk.circuit_breakers.weekly_size_down`
- Drawdown: from latest `portfolio_nav.drawdown_pct` vs `risk.circuit_breakers.drawdown_kill`

Values sourced from `portfolio_nav` (today + 5 days ago + rolling peak).

**Section 2 — Tail Risk KPIs**

Row: VIX, VIX 30d avg, Credit Spread (HYG–TLT proxy from `daily_prices`), Credit Spread z-score.

**Section 3 — Risk Decomposition Donut**

Pie chart (Plotly): factor variance vs specific variance.  
Sourced from `cache/predicted_cov_latest.parquet` (written by Layer 5).  
Factor variance = sum of diagonal of `B @ F @ B.T`; specific = sum of diagonal of `D`.

**Section 4 — Factor Risk Contributions Table**

Table: factor name, contribution to portfolio variance (%), marginal contribution.  
From `predicted_cov_latest.parquet`.

**Section 5 — MCTR Table**

Marginal Contribution to Risk per position.  
Flag positions where MCTR > `config.risk.circuit_breakers.max_single_position_pct * 1.5`.  
Computed from covariance matrix × current portfolio weights.

**Section 6 — Factor Exposure Bars**

Horizontal bars for each of 8 factors, showing portfolio-weighted average z-score.  
Red warning annotation if `|z| > config.risk.factor_monitor.alert_z_threshold`.

**Section 7 — Stress Test Table**

6 scenarios from `config.risk.stress.scenarios`.  
Source: rerun `risk.stress_test.run_all()` or read from `risk_log` where `check_type='stress'`.  
Columns: scenario, estimated loss ($), estimated loss (%), confidence.

**Section 8 — Correlation Heatmap**

60-day pairwise correlation of current positions using `daily_prices`.  
Plotly heatmap. Below chart: "Effective Bets" = `1 / mean(|pairwise_corr|)`.

**Section 9 — 72-Hour Alerts**

`risk_log` rows from last 72 hours where result in ('WARNING', 'REJECTED', 'TRIGGERED').  
Shown as coloured alert boxes.

---

### Page IV — Performance (`dashboard/page_performance.py`)

```python
def render(engine) -> None:
```

**Section 1 — Equity Curve**

Plotly line chart. Two traces:
- Portfolio NAV rebased to 100 (from `portfolio_nav`)
- SPY rebased to 100 (from `daily_prices`)

x-axis: date, y-axis: index value.

**Section 2 — Monthly Returns Grid**

Pivot: rows=year, cols=Jan–Dec + Annual.  
Source: `portfolio_nav` daily returns aggregated to monthly.  
Rendered via `st.dataframe` with Styler — green for positive, red for negative, intensity proportional to magnitude (diverging at 0).

**Section 3 — Drawdown Chart**

Plotly area chart (filled red) of `portfolio_nav.drawdown_pct` over time.

**Section 4 — P&L Attribution**

Stacked bar chart (Plotly): one bar per day, coloured segments for Beta / Sector / Factor / Alpha.  
Source: `pnl_attribution`. Limit to last 90 days; slider to extend.

**Section 5 — Rolling 12-Month Sharpe**

Line chart: 252-day rolling Sharpe computed from `portfolio_nav.nav` daily returns.  
Reference line at 0 and at 1.0.

**Section 6 — Sector-Relative Alpha**

Bar chart: one bar per sector, showing 90d alpha vs sector ETF (from `sector_performance.compute()`).  
Total alpha KPI above chart. Below chart: winner sectors (green), loser sectors (red) with counts.

**Section 7 — Turnover Panel**

4 KPI tiles: 30d turnover %, annualised %, budget %, tax estimate $.  
Source: `turnover.compute()`.

**Section 8 — Transaction Cost Panel**

Table: estimated cost (from Layer 4 `portfolio.transaction_costs` model), actual slippage cost (from `execution_orders.slippage_bps × filled_shares × avg_fill_price / 10000`), model error = actual − estimated.

**Section 9 — Best/Worst 5 Contributors**

Two `st.dataframe` tables side by side.  
Source: `position_trades` closed trades sorted by `realized_pnl` desc / asc.  
Columns: ticker, direction, holding_days, entry_price, exit_price, realized_pnl.

**Section 10 — Win/Loss Panel**

From `win_loss.compute()`:
- Overall win rate, P/L ratio
- `st.tabs` for: By Side | By Holding Period | By Sector | By VIX Regime | By Factor Quintile
- Streak display

**Section 11 — JARVIS Weekly Commentary**

Card showing latest `weekly_commentary.content` rendered as markdown.  
"Week of {week_start}" label. Regenerate button (sets `force=True`).

---

### Page V — Execution (`dashboard/page_execution.py`)

```python
def render(engine) -> None:
```

**Section 1 — KPI Row**

4 tiles:
- Filled Orders 30d: count from `execution_orders` where `status='FILLED'` and `created_at >= 30d ago`
- Avg Slippage bps: `execution.costs.slippage_stats(conn, 30)['mean_bps']`
- Total Slippage $: sum of `slippage_bps / 10000 * filled_shares * avg_fill_price` for FILLED last 30d
- Open Orders: count where `status='PENDING'` or `status='PARTIAL'`

**Section 2 — Open Orders Table**

Poll Alpaca via `execution.broker.AlpacaClient().get_open_orders()`.  
Columns: ticker, action, ordered_shares, filled_shares, status, created_at.  
Auto-refresh: `st.button("Refresh")` re-queries.

**Section 3 — Recent Trades Log**

Last 200 rows of `execution_orders` ordered by `created_at desc`.  
Columns: date, ticker, action, ordered, filled, avg_price, slippage_bps, status.  
Highlight FAILED and CANCELLED rows in red.

**Section 4 — Worst 5 Fills**

Top 5 by slippage_bps from `execution_orders` (FILLED only, last 90d).

**Section 5 — Short Availability Panel**

For each ticker in `portfolio_positions` where `direction='SHORT'`:  
Call `execution.short_check.is_shortable(ticker)` (uses 7-day cache).  
Display as table: ticker, shortable (✓/✗), days in cache.

**Section 6 — Daily Notional Turnover**

Group `execution_orders` by `rebalance_date`, sum `filled_shares * avg_fill_price` → daily notional.  
Last 30 rows as bar chart.

---

### Page VI — Letter (`dashboard/page_letter.py`)

```python
def render(engine) -> None:
```

**Letter card styling:** warm off-white background (`#f8f6f1`), dark text (`#1a1a1a`), warm border (`#d4c9b0`), border-radius 8px. Georgia serif font. CONFIDENTIAL stamp in dark red (`#8b1a1a`). Subheadings in muted grey (`#5a5a5a`). Deliberately light — designed to read like real letterhead against the dark dashboard.

**Letterhead block:**

```
MERIDIAN CAPITAL PARTNERS
Wilmington, Delaware  ·  Inception: {inception_date}  ·  AUM: ${nav:,.0f}
Doc: MCP-IM-{YYYY}-{MMDD}                                        {date}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CONFIDENTIAL · LIMITED PARTNERS ONLY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Body:** "Dear Limited Partners," followed by `lp_letters.content` for today rendered as markdown paragraphs.

**Signature:**
```
Respectfully,

JARVIS
Portfolio Intelligence System
Meridian Capital Partners
```

**Compliance footer:** standard disclaimer paragraph — not investment advice, for accredited investors only, past performance not indicative of future results, subject to material risks.

**"Regenerate letter" button:** calls `lp_letter.generate(engine, cfg, force=True)`, spinner, then reruns.

Letter is cached by date in `lp_letters` table; first load generates if not cached.

---

## Config Additions (`config.yaml`)

Add new top-level `reporting:` section:

```yaml
reporting:
  commentary_weekday: 4        # 0=Mon … 4=Fri
  inception_date: "2024-01-02" # for letterhead
  tax_rates:
    short_term: 0.37
    long_term:  0.20
  tear_sheet_path: output/tear_sheet.md
  attribution_csv: output/daily_attribution.csv
```

---

## Requirements Additions (`requirements.txt`)

```
streamlit>=1.35
plotly>=5.22
pytz>=2024.1
scipy>=1.13
```

`openai` is already a dependency from Layer 3. No new AI SDK needed.

---

## Daily Automation

The prompt specifies a macOS launchd plist; however the runtime environment is Linux (PVE). Provide both:

### Linux cron (`crontab -e`)

```cron
15 17 * * 1-5 cd /home/david/hedge/ls_equity_fund && python run_scoring.py --no-filings --no-13f >> output/run.log 2>&1
20 17 * * 1-5 cd /home/david/hedge/ls_equity_fund && python run_reporting.py --all >> output/run.log 2>&1
```

### macOS launchd plist (reference: `~/Library/LaunchAgents/com.user.hedgefund.daily.plist`)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>            <string>com.user.hedgefund.daily</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-c</string>
    <string>
      cd /home/david/hedge/ls_equity_fund &amp;&amp;
      python run_scoring.py --no-filings --no-13f &amp;&amp;
      python run_reporting.py --all
    </string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>    <integer>17</integer>
    <key>Minute</key>  <integer>15</integer>
    <key>Weekday</key> <integer>0</integer>
  </dict>
  <key>StandardOutPath</key>  <string>/tmp/hedgefund.out</string>
  <key>StandardErrorPath</key><string>/tmp/hedgefund.err</string>
</dict>
</plist>
```

Note: launchd `Weekday=0` means Monday; for Mon–Fri, the plist would need five separate entries or a script-level weekday check. The Linux cron `* * 1-5` is cleaner.

---

## Testing

### `tests/test_reporting_nav.py`

- `test_build_nav_series_empty` — empty `portfolio_history` → empty DataFrame, no crash
- `test_build_nav_series_known_values` — fixture with 3 snapshot dates, assert NAV = `nav_usd + sum(unrealized_pnl)` per day
- `test_drawdown_calculation` — assert drawdown correct on simulated peak/trough

### `tests/test_reporting_attribution.py`

- `test_beta_pnl_formula` — assert `beta_pnl = net_beta * spy_return` to 6 decimal places
- `test_alpha_residual_sums_to_total` — assert `beta + sector + factor + alpha ≈ total_return` (float tol 1e-9)
- `test_brinson_zero_when_weights_match_benchmark` — allocation effect = 0 when weights match

### `tests/test_reporting_win_loss.py`

- `test_win_rate_all_winners` — 5 winning trades → win_rate = 1.0
- `test_win_rate_no_trades` — empty → return zeroed dict, no KeyError
- `test_holding_period_bucketing` — 3 trades at 2d, 10d, 50d → correct bucket counts

### `tests/test_reporting_fifo.py`

- `test_fifo_full_exit` — enter 100 shares, exit all → one closed trade, `realized_pnl` correct
- `test_fifo_partial_exit` — enter 100, exit 60 → one closed 60-share trade, one open 40-share trade
- `test_fifo_direction_flip` — long → short → treat as close + new short

---

## Implementation Order

1. `reporting/db.py` — define 5 tables, extend `data.db.initialise_schema`
2. `reporting/nav_series.py` — unblocks all performance metrics
3. `reporting/pnl_attribution.py` — depends on nav_series
4. `reporting/position_attribution.py` — FIFO logic; most complex module
5. `reporting/win_loss.py`, `sector_performance.py`, `turnover.py` — pure analytics, no new DB writes
6. `reporting/tear_sheet.py`, `commentary.py`, `lp_letter.py` — Claude calls; add after analytics
7. `run_reporting.py` — wires modules together
8. `dashboard/theme.py`, `dashboard/jarvis.py` — foundation before pages
9. `dashboard/app.py` — nav shell
10. Pages I → VI in order
11. Tests
12. Cron entry

---

## Key Data Dependencies Summary

| Layer 7 module | Primary source tables |
|---|---|
| nav_series | `portfolio_history`, `daily_prices` (SPY) |
| pnl_attribution | `portfolio_nav`, `portfolio_history`, `factor_scores`, `daily_prices` |
| position_attribution | `portfolio_history`, `combined_scores`, `daily_prices` |
| win_loss | `position_trades`, `daily_prices` (VIX) |
| sector_performance | `portfolio_history`, `daily_prices` (sector ETFs), `sp500_universe` |
| turnover | `portfolio_history`, `portfolio_nav`, `position_trades` |
| tear_sheet | All of the above + `execution_orders` |
| commentary / lp_letter | `pnl_attribution`, `portfolio_nav`, `risk_events`, `portfolio_positions` |
| page_portfolio | `sp500_universe`, `factor_scores`, `portfolio_positions`, `daily_prices`, `insider_transactions`, `earnings_calendar`, `insider_cluster_flags` |
| page_research | `factor_scores`, `position_approvals`, `combined_scores`, `analysis_results`, `earnings_calendar` |
| page_risk | `portfolio_positions`, `daily_prices`, `risk_log`, `risk_events`, `portfolio_nav`, `cache/predicted_cov_latest.parquet` |
| page_performance | `portfolio_nav`, `pnl_attribution`, `position_trades` |
| page_execution | `execution_orders` |
| page_letter | `lp_letters` |
