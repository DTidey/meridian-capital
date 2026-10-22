# Fundamentals and Value Inputs

**Spec file:** `docs/specs/42-fundamentals-value-inputs.md`
**Spec slug:** fundamentals-value-inputs
**Status:** Draft
**Owner:** DTidey
**Date:** 2026-10-22

## Problem statement
The 2026-10-21 data audit (`docs/data-audit-2026-10-21.md`, items C2, H6, H7, M12 and M14)
found that the value and quality factors run on wrong inputs. Rechecked against production and
Yahoo on 2026-10-22:

- **Market cap comes from the balance-sheet share count, which is wrong for some tickers (C2).**
  - BRK-B is stored as 1.43M shares (the Class A equivalent). The Class B equivalent is about
    2.14B, so market cap is understated about 1500x. That gives a book/price of 981x, a
    composite of 98.7, and LONG.
  - CME's 2026-06-30 count is 359,275 against 361.8M the quarter before (a one-quarter 1000x
    glitch), making it LONG at 90.8.
  - **Correction to the audit:** MNST and APH are not doubled. MNST split 2:1 on 2026-08-11 and
    APH on 2026-09-03. The stored counts are split-adjusted, like our prices, and the SEC
    cover-page counts the audit compared against predate the splits.
  - SEC cover-page counts can't fix this. Multi-class filers report per class, so XBRL has no
    single figure for them: 80 universe tickers, including BRK-B, GOOGL, CME and FOXA, are
    missing from SEC's 2026 Q2 cover-page frame.
  - Yahoo's `.info` `marketCap` is right for all of these (BRK-B $1.15T, GOOGL $4.09T across
    all classes, CME $106B, FOXA $27.8B across both classes). We already fetch `.info` every
    night for short interest and estimates.
- **The value factor uses one quarter, mixed across periods (H6, M14).**
  - `factors/value.py:39` takes `groupby("ticker").last()`, which picks the last non-null value
    per column. One "row" can mix quarters.
  - FCF, EBIT, revenue, dividends and buybacks are single-quarter figures. Moving to trailing
    twelve months (TTM) shifts 134 tickers by more than 20 points on FCF yield.
- **Missing lines become NaN, not zero (H6).**
  - `fund.get("dividends_paid") or 0.0` keeps NaN, because NaN is truthy. Shareholder yield is
    then NaN and gets filled with a neutral 50 for 143 tickers, including CMG's $654M buyback.
  - Yahoo omits a cash-flow line when it is zero. In the latest quarter 133 tickers have no
    dividend line and 136 have no buyback line. 44 have no debt line.
- **EBIT is not operating EBIT (H6).**
  - `data/fundamentals.py:62` prefers Yahoo's "EBIT" row, which includes non-operating gains.
    GOOGL's 2026-06-30 EBIT is $140.0B against $40.8B operating income and $119.8B revenue.
  - For 75 tickers, EBIT and operating income differ by more than 25%.
- **ROE is stored as the true value minus one (H7).**
  - `_compute_ratios` uses `_pct_change(ni, eq)`, which gives `ni/|eq| - 1`. AAPL is stored as
    -0.72 instead of 0.28.
  - For negative-equity companies the sign flips. The dashboard shows these values.
- **Negative equity gives a negative D/E (M12).** The most leveraged names (LOW, MAR, DPZ, YUM;
  28 tickers have negative equity) rank as the least leveraged.

Goal: value and quality use a correct market cap, trailing-twelve-month flows from one
consistent set of quarters, operating income as EBIT, and correct ROE and D/E.

## Scope
In scope:
- A new table, `market_caps`: Yahoo `.info` `marketCap` per ticker per day, stored by a new
  Layer 1 step that reuses the run's shared `.info` fetch.
- A shared market cap helper: effective shares from the stored market cap, with a one-day
  glitch guard and a fallback to balance-sheet shares.
- `factors/value.py`:
  - market cap from the helper;
  - TTM flows (revenue, operating income, FCF, dividends, buybacks);
  - balance-sheet items from one quarter;
  - zero for an omitted line when its statement is present.
- `factors/quality.py`:
  - Altman Z market cap from the helper, and its EBIT term from operating income;
  - ROE stability from net income / equity;
  - D/E for negative equity.
- `data/fundamentals.py`: the ROE and D/E formulas.
- `run_data.py --recompute-ratios`: a one-off recompute of stored `roe` and `debt_to_equity`.
- Dashboard ticker valuation:
  - the same market cap helper;
  - TTM helper moved to a shared module;
  - EV/EBIT from operating income.
- `docs/data-audit-2026-10-21.md`: correct C2's MNST/APH evidence and its Verify step.

Out of scope / non-goals:
- The stored `ebit` column. It keeps Yahoo's EBIT row; consumers switch to `operating_income`.
- Balance-sheet share history. CME's 2026-06-30 glitch row stays stored, and Piotroski's
  dilution signal still reads it. A backlog item is added.
- Altman Z's mix of quarterly figures with annual coefficients, and sector-specific factors
  (M13).
- Fundamentals lag (M10), filed dates (M11), the FMP backend.

## Assumptions
- `daily_prices.close` is split-adjusted but not dividend-adjusted, as is Yahoo's price behind
  `marketCap`. So `marketCap / close` on the snapshot date is the share count in our price's
  units, and it is unchanged by a later split once packet 38 re-downloads the prices.
- Yahoo `marketCap` covers every share class of the company, priced at this listing's price.
  This was checked for BRK-B, GOOGL, FOXA and NWSA.
- A missing dividend or buyback line in a quarter whose cash-flow statement exists (non-null
  `cfo`) means zero. A missing `total_debt` in a quarter whose balance sheet exists (non-null
  `total_assets`) means zero.
- Quarterly ROE (one quarter's net income over equity) is kept. Annualising it is not in scope.

## Proposed behavior / API
### Public interface
- `data/db.py`: table `market_caps (ticker, date, market_cap, fetched_at)`, primary key
  `(ticker, date)`.
- `data/market_caps.py`: `update_market_caps(conn, tickers, info)`.
  - Fetches only tickers without today's row.
  - Uses the shared `YahooInfo` cache, so it makes no extra requests when short interest or
    estimates already fetched the ticker.
  - Logs coverage with `log_coverage`.
- `run_data.py`: a market caps step after short interest; `--recompute-ratios`.
- `factors/loader.py`: loads `market_caps` up to the score date into `data["market_caps"]`.
- `factors/market_cap.py`: `market_caps(prices, snapshots, fundamentals) -> pd.Series` (per
  ticker, used by value and quality).
  - It also exposes the single-ticker form the dashboard uses.
- `factors/ttm.py` (moved from `dashboard/ticker_valuation.py`): `ttm(quarters, field)`.
  - Same contiguity rules as today: four latest reporting quarters, span at most 300 days,
    newest at most 100 days behind the latest quarter.
  - Adds the zero-for-omitted-line rule for `dividends_paid` and `buybacks`.

### Behavior
- **Effective shares** for a ticker:
  - From each of its two latest snapshots: `S = market_cap / close` on the snapshot date, or the
    nearest earlier bar.
  - If the latest `S` differs from the previous `S` by a factor of more than 1.5 either way,
    use the previous `S` (a one-snapshot glitch). Otherwise use the latest.
  - With one snapshot, use it.
  - With no snapshot, fall back to `shares_outstanding` from the latest quarter that has a
    balance sheet. Log the fallback count once per run.
- **Market cap** = effective shares x latest close.
- **Value factor:**
  - Balance-sheet items (`total_equity`, `total_debt`, `cash`) come from the latest quarter
    with `total_assets`. A missing `total_debt` there is 0.
  - `EV = market cap + debt - cash`; `None` if cash is missing.
  - `val_book_to_price = equity / market cap`.
  - `val_fcf_yield = TTM fcf / market cap`.
  - `val_ev_ebitda_inv = TTM operating_income / EV` (when both are positive).
  - `val_sales_to_ev = TTM revenue / EV`.
  - `val_shareholder_yield = (TTM |dividends| + TTM |buybacks|) / market cap`. It is NaN only
    when neither TTM is available. If one is available, the other counts as 0.
  - A missing TTM gives NaN for that sub-factor, not a single-quarter fallback.
- **Quality factor:**
  - `_altman_z` uses the shared market cap and `operating_income`.
  - `_roe_stability` uses `net_income / total_equity` per quarter, skipping quarters with
    equity <= 0.
  - `_de_inv`: equity <= 0 with debt > 0 gives the lowest value in the sector (most
    leveraged); equity <= 0 with no debt gives NaN.
- **Ratios at ingestion:**
  - `roe = net_income / total_equity` when equity > 0, else `None`.
  - `debt_to_equity = total_debt / total_equity` when equity > 0, else `None`. Zero debt gives
    0.0.
- **`--recompute-ratios`:**
  - Recomputes `roe` and `debt_to_equity` for every stored fundamentals row from its stored raw
    figures. Other columns are untouched.
  - Idempotent. Logs the rows changed.
  - Cannot be combined with `--tickers`, like `--rebuild-insider`.

### Error handling
- A ticker whose `.info` fetch fails gets no row that day. The helper uses the earlier
  snapshots.
- A `marketCap` that is missing, zero or negative is not stored.

## Acceptance criteria
- AC1: The market caps step stores one `market_caps` row per fetched ticker per day from
  `.info` `marketCap`.
  - It skips tickers that already have today's row and adds no `.info` request for a ticker
    already fetched this run.
  - It does not store a missing or non-positive value.
- AC2: Effective shares follow the rules above:
  - the latest snapshot is used when within 1.5x of the previous one;
  - the previous snapshot is used when not;
  - a single snapshot is used as is;
  - otherwise balance-sheet shares are used.
  - A split between snapshots does not trigger the guard once prices are split-adjusted.
- AC3: The value factor and Altman Z use that market cap. With BRK-B's balance-sheet count of
  1.43M and a `marketCap` of $1.15T, `val_book_to_price` is computed from $1.15T.
- AC4: Value flows are TTM sums over four contiguous quarters. With fewer quarters or a gap,
  the sub-factor is NaN.
- AC5: A dividend or buyback line omitted from a quarter with a cash-flow statement counts as
  zero.
  - A ticker with buybacks and no dividend line gets a shareholder yield; NaN only when neither
    is available.
  - No value input is NaN because of `x or 0.0` on a NaN.
- AC6: EV/EBIT in the value factor, Altman Z and the dashboard use operating income, not the
  stored `ebit`.
- AC7: Balance-sheet inputs to the value factor come from a single quarter: the latest with
  `total_assets`. Columns are never taken from different quarters.
- AC8: Ingestion stores `roe = net_income / total_equity` and `debt_to_equity = total_debt /
  total_equity` for equity > 0, and `None` for equity <= 0.
  - Quality's ROE stability uses net income / equity.
  - A negative-equity ticker with debt ranks lowest in its sector on `qual_de_inv`.
- AC9: `run_data.py --recompute-ratios` rewrites stored `roe` and `debt_to_equity` with the AC8
  formulas.
  - It leaves other columns unchanged and is idempotent (a second run changes 0 rows).
  - It rejects `--tickers`.
- AC10: The dashboard ticker valuation uses the shared market cap and TTM helpers. Its
  behavior is otherwise unchanged (existing valuation tests pass, apart from the EBIT source).
- AC11: `make lint`, `make test` and `make security` pass.

## Security considerations
- Auth/authz: no change.
- Input handling or injection risk: Yahoo values are parsed with the existing `_safe`-style
  float checks; SQL goes through SQLAlchemy Core.
- Secrets, data exposure, file system: no change.
- Network or external service impact: no new requests; `marketCap` comes from `.info` responses
  already fetched each night.
- Dependency or supply-chain impact: none.
- Security notes for reviewers/testers: `--recompute-ratios` writes to production
  fundamentals. It is idempotent and touches only two derived columns, and runs only with the
  owner's go-ahead.

## Edge cases
- First night after deployment: one snapshot per ticker, used as is.
- A ticker with no snapshot (new to the universe, or `.info` failed every night so far):
  balance-sheet fallback.
- Snapshot dated on a non-trading day: uses the nearest earlier close.
- A glitch on two consecutive snapshots is accepted on the second day (same limit as the
  revisions guard).
- A split on the same day as the snapshot, before packet 38 re-downloads prices: the old and new
  `S` disagree by about 2x for one day and the guard keeps the previous value.
- A non-payer with a cash-flow statement: dividend TTM 0, not NaN.
- A quarter with no cash-flow statement inside the four: its omitted lines are not zero, so the
  TTM is missing.
- Equity exactly 0: ROE and D/E are `None`.
- A newest quarter with an income statement but no balance sheet: flows still use it in the
  TTM; balance-sheet items come from the quarter before.

## Test guidance
- AC1 -> `tests/test_market_caps.py` (store, skip today's, shared cache, bad values)
- AC2 -> `tests/test_market_cap_helper.py` (latest, glitch, single, fallback, split)
- AC3 -> `tests/test_value*.py` (BRK-B fixture), `tests/test_quality*.py` (Altman Z)
- AC4, AC5, AC7 -> `tests/test_value*.py`, `tests/test_ttm.py`
- AC6 -> value, quality and dashboard valuation tests with EBIT != operating income
- AC8 -> `tests/test_fundamentals*.py` (`_compute_ratios`), quality tests
- AC9 -> `tests/test_recompute_ratios.py` (SQLite fixture, idempotence, CLI rejects `--tickers`)
- AC10 -> existing `tests/test_ticker_valuation*.py`
- AC11 -> `make lint`, `make test`, `make security`

## Production verification (after merge, owner's go-ahead)
1. Deploy, then run `run_data.py --recompute-ratios`. Check AAPL 2026-06-30 `roe` is about
   0.277.
2. Let the nightly run store the first `market_caps` rows.
3. Rescore and check:
   - BRK-B and CME market cap within 5% of Yahoo `marketCap`;
   - BRK-B book/price and composite are no longer extreme;
   - GOOGL's EV/EBIT uses about $40.8B a quarter;
   - CMG has a shareholder yield.
4. Update C2, H6 and H7 in `docs/data-audit-2026-10-21.md`.

## Decision log
- 2026-10-22: Market cap from Yahoo `.info` `marketCap`, not SEC cover-page counts. SEC has no
  single figure for multi-class filers, and `.info` is already fetched nightly.
- 2026-10-22: Store market cap and derive shares with our close, rather than storing Yahoo's
  share count. The derived count is unaffected by splits, because our prices are split-adjusted
  retroactively.
- 2026-10-22: Consumers switch to `operating_income` instead of rewriting the stored `ebit`
  column, so no fundamentals re-fetch is needed.
- 2026-10-22: Quality computes ROE from raw figures, so ROE stability does not mix old and new
  stored formulas while stored rows await `--recompute-ratios`.
