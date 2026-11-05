# Investigation: under-invested book and Layer 4 / Layer 6 trade mismatch (2026-11-05)

**Scope:** paper trading 2026-10-27 to 2026-11-04, focus on the 2026-11-04 nightly run.
**Code:** origin/main `051dfb6`. David's live checkout is on the same commit.
**Evidence:** logs in `/home/david/hedge/ls_equity_fund/logs/`, read-only SELECTs against
`meridian`, and a read-only replay of the 2026-11-04 Layer 4 run (optimiser plus
`generate_trades(commit=False)` inside a `READ ONLY` transaction). The replay reproduced the 16
logged trades exactly (and the other six nights' trade counts, 8/11/14/16/16/12). Read-only
Alpaca GETs (orders, positions) on 2026-11-05 at 15:13 UTC. Prior Claude Code sessions in
`~/.claude/projects/-home-david-hedge/` were searched for design history (cited by session id
prefix and UTC timestamp; every claim taken from them was re-checked against the code).
Nothing was written to the database or sent to Alpaca.
**Reproduction tests:** `tests/test_investigation_2026_11_05_repro.py`. Four tests, all
`xfail(strict=True)`; each fails on its assertion today.

## Prioritised summary

| # | Finding | Effect | Confidence | Fix size |
|---|---|---|---|---|
| 1 | The 30% turnover budget applies **per run** and counts the build-up from cash as turnover. On 11-04, trades worth **125% of NAV** were needed; the budget kept **28.6%** and cut 24 of 40 trades to HOLD, 22 of them names not yet held. The go-live estimate of "about four nights" to fill the book (sessions 630ff079, 9d2a0c48) assumed every budgeted trade would be sent and filled in full; it did not allow for #2 and #3 or for churn. | Main cap on investment | Confirmed | Small to medium |
| 2 | Layer 5 shrinks the trades that survive, applied to the **target**, not the delta: an earnings blackout halves the target, then tail-risk REDUCE_GROSS_20 takes 0.8x. REDUCE_GROSS_20 has fired on every run so far (credit z between 1.3 and 2.7, VIX below 25), partly because the FRED lookback holds ~205 observations, not 252. On 11-04 the submitted value fell from 28.6% to **16.1%** of NAV, and 4 of 16 trades reversed direction. | Second cap; also causes churn | Confirmed | Small to medium |
| 3 | DAY limit orders are priced at close ±0.5% and wait for the next session. Any overnight gap of more than 0.5% the wrong way means no fill. From 10-27 to 11-03, 43 of 65 orders filled (66%; 19 expired, 3 failed), which is **~71% of order value** ($64.3k of $90.3k). | Third cap; adverse selection | Confirmed | Small |
| 4 | Q2 direction flips (CLX, AKAM, CF, NKE, plus AIZ/AXON/COHR sizes): blackout x0.5 and reduce_gross x0.8, compounded on the target. The executor and "current shares" are both correct. | Explains every Q2 mismatch | Confirmed | See #2 |
| 5 | Earnings are haircut three times (Layer 4 optimiser, pre-trade, then reduce_gross on the result). The two windows also differ (forward-only vs ±5 days). | Small positions near earnings | Confirmed | Small |
| 6 | Tear sheet: inception date is hard-coded, a stale 2026-05-21 NAV row is included, beta is hard-coded to 0, the "factor exposures" are ZTS's own scores, and slippage is measured against the limit price. | Reporting only | Confirmed | Small each |
| 7 | Historical errors: 10-26 book sync fixed by packet 53, 10-28 double CLX cover by packet 54, 10-29 "qty must be > 0" by packet 56. One minor latent risk remains (duplicate APPROVED rows if Layer 4 is re-run). | None now | Confirmed / likely | Small |

Net effect on 11-04, stage by stage: **125% of NAV needed -> 28.6% after the turnover budget
-> 16.1% after Layer 5 -> ~71% historical fill rate by value**. The book was at 26.8% gross. If
Layer 4's 16 trades had gone through and filled, gross would have reached 53.6%. After Layer 5
the orders could reach at most 39.1%. In fact, by 15:13 UTC on 11-05, 11 of the 15 orders had
filled (CVNA 4 of 10; KIM 154, COIN 4, AIZ 2 and TXN 1 still open), and the Alpaca book was 17
positions at about 32.7% gross. Since 10-29, gross has been flat at about 28-31%, because each
night's filled opens ($3-7k) are offset by filled closes ($3-8k).

---

## Q1. Why the live book is far below target

### How the target turns into orders (11-04)

| Stage | Code | Names / rows | Trade value | Resulting gross if filled |
|---|---|---|---|---|
| Layer 4 target | `portfolio/optimizer.py:20-139` | 40 (20L/20S), 150% gross | n/a | 150% |
| Diff vs book | `portfolio/rebalance.py:49-84` | 40 non-HOLD trades | $124.8k = **125.3% NAV** | 150% |
| Turnover budget 30% | `portfolio/rebalance.py:90`, `:142-197` | 16 kept, **24 set to HOLD** | $28.5k kept (28.6%), $96.3k trimmed (96.7%) | 53.6% |
| Approvals written | `portfolio/rebalance.py:218` (skips HOLD) | 16 rows | | |
| Pre-trade veto | `risk/pre_trade.py:143-154` | 16 approved, 6 blackout-resized | | |
| Tail risk REDUCE_GROSS_20 | `risk/tail_risk.py:333-384` | 12 rows rescaled | $16.1k submitted (16.1%) | 39.1% |
| Executor | `execution/executor.py:186-220` | 15 orders, 1 HOLD skip | | |
| Fills (~71% of value historically) | `execution/executor.py:103-110` | 11 of 15 filled by 11-05 15:13 UTC | | 32.7% actual (Alpaca, 11-05) |

The current book is 26.8% gross ($26,663 / $99,601 NAV, `portfolio_positions` as of
2026-11-04T22:10:38).

### Constraint 1: turnover budget (the main cap)

- `config.yaml:176` sets `turnover_budget_pct: 0.30`. `_apply_turnover_budget`
  (`portfolio/rebalance.py:142-197`) computes `sum(|delta| x price) / NAV` for **this run's**
  trades. If that exceeds 30%, it keeps closures and fills the rest greedily within the 30%.
  Everything that does not fit becomes `action="HOLD"`, `delta=0` (`:192-194`), and
  `_write_approvals` drops HOLD rows (`:218`). Those names get no approval row and no order,
  and nothing records that they were trimmed.
- Nothing distinguishes building a book from cash from rebalancing a full book. With the book at
  27% and a target of 150%, the gap alone is 123% of NAV. That needs at least 5 nights at 30%
  even with no other loss.
- The budget is split between the long and short books in proportion to trade value
  (`:167-169`). Each book is then filled by `|combined_score - 50|`, highest first. The loop
  **skips** a trade that does not fit and keeps going (`:179-184`). So a large trade for a new
  name (about $4-5k for a 3.75-5% position) loses to small top-ups of existing names. On 11-04,
  AXON (conviction 45.3, $1.7k) was kept while DVA (47.3, $5.0k) and MOS (47.0, $4.6k) were
  trimmed. 12 of the 16 kept trades were changes to names already held.
- Trimmed on 11-04: the new longs CSX, AWK, LLY, NDSN, ABBV, RVTY, ACGL, KEYS, SWKS, FOXA, ALLE
  and AMGN; the new shorts DVA, MOS, ABT, LII, PEG, LVS, BX, HII, MCO and ETN; and the adds to
  SWK and URI.
- There is **no separate cap on new positions**. The new-name cap is a side effect of the
  budget.
- Reproduction: `test_build_from_cash_not_capped_by_turnover_budget`. A 40-name, 150% target
  from an empty book produces 6 trades.
- **Spec:** `docs/specs/04-portfolio-construction.md` AC7 and the config comment at line 167
  ("max fraction of portfolio turned per rebalance") make a per-rebalance budget intended. The
  spec never considers the build-up. The code also contradicts AC7 and spec section line 432:
  the spec says trim by "smallest |Δscore| first", but the code ranks by `|score - 50|` and the
  greedy loop skips trades that do not fit.
- **The tear sheet's "32.2% vs 30%" is a unit mismatch, not a gate.** `reporting/turnover.py:75`
  computes a **30-day** turnover, and `reporting/tear_sheet.py:233-239` prints it next to the
  **per-run** budget. No code gates on the 30-day number.

**Fix (small to medium):** leave the build-up out of the budget. Options:
(a) count only trades that reduce or rotate existing exposure, so opening up to the target gross
is free; (b) budget against the *gap* to target gross, for example `max(budget, |target_gross -
current_gross|)`; (c) a one-off ramp mode while `current_gross < 0.8 x target_gross`. (a) is the
simplest. Also make the fill stop at the first trade that does not fit (or document the skip
behaviour), and record trimmed names (approval rows with status `TRIMMED`) so the shortfall
shows up in the run output. Spec 04 AC7 needs updating either way.

### The "about four nights" assumption, and why it did not hold

- The slow build was known before go-live. The scores audit pasted into session 5978daab
  (2026-10-21T13:10Z, item H4 "Initial build and turnover", HIGH) said the 30% daily budget
  against 150% gross means "the first build takes at least 5 days", that targets also move
  daily, and suggested exempting the initial build, rebalancing weekly or adding hysteresis.
  Nobody acted on it; later checklists reused the label "H4" for the net-exposure fix.
- In session 630ff079 (2026-10-24T17:43Z) the assistant told the user that "each rebalance can
  trade only 30% of NAV, so building the full book ... takes about four nights". The user
  replied "Let's fix H4" (the net-exposure item), so the slow build was implicitly accepted. The
  same estimate was given at go-live, when the cron `--whatif` was removed (9d2a0c48,
  2026-10-27T08:41-08:42Z).
- Why it did not hold: the estimate treated 30% a night as 30% of new gross a night. In
  practice, over the six completed nights (10-27 to 11-03):
  - the budget kept 170% of NAV of trades;
  - Layer 5 cut that to 90% sent (blackout x0.5, REDUCE_GROSS x0.8, 4 POSITION_SIZE rejects);
  - ~71% filled by value (64% of NAV);
  - 30% of the filled value ($19.2k) was closes or reductions: names cut by Layer 5 on
    previous nights are topped up again, and moving targets cause reversals (CSGP, ALLE, CLX).
  Net gross added: about 27% of NAV after six nights, against the 150% target.
- No later session (up to f1142635, 2026-11-04T11:56Z, which reported "10-12 positions" against
  40 targeted) flagged the book as under target.

### Constraint 2: Layer 5 resizes applied to the target (and every night)

- Earnings blackout: `risk/pre_trade.py:143-154` sets `target = round(target x 0.5)` for any
  opening trade (BUY/SHORT) whose ticker has earnings within ±5 days (`:518-531`;
  `config.yaml:214`). Six of the 16 rows on 11-04: AIZ, AKAM, AXON, CF, CLX, COHR.
- Tail risk: `risk/tail_risk.py:358-381` sets `target = round(target x 0.8)` for every APPROVED
  BUY/SHORT row. It runs **after** pre-trade (`run_risk_check.py:324-338`), so blackout names
  end at 0.4x target. CAUTION / REDUCE_GROSS_20 fired on **every** run from 10-26 to 11-04,
  always on credit z (1.32-2.70, against `credit_spread_sigma: 1.0`, `config.yaml:226`); VIX was
  16.6-21.2, below the 25 threshold.
- Why CAUTION never clears: `credit_spread_sigma: 1.0` against the HY OAS from FRED
  (`BAMLH0A0HYM2`, used because `FRED_API_KEY` is set). `_get_credit_spread_z_fred`
  (`risk/tail_risk.py:223-257`) fetches from `score_date - (252 + 30)` **calendar** days and then
  takes `.tail(252)` **observations**, so only about 195-205 business-day observations are used
  (the 11-04 cache `cache/fred_hy_spread.parquet` holds 205, from 2026-01-26 to 2026-11-03), not
  the 252 that spec 05 (line 532, "vs 252-day mean/std") and the config name imply. That window
  is a tight-spread period (mean 2.89%, std 0.21%), so a spread of 3.40% scores z = 2.42. A
  1-sigma threshold on a ~9-month window fires on any 20bp widening. CAUTION has fired every
  night since mid-October (sessions 8ef22c9b 2026-10-23T17:17Z, 630ff079 2026-10-24T15:01Z,
  284a3bba 2026-10-30T07:34Z, f1142635 2026-11-04T11:56Z). Fix (small): fetch enough calendar
  days for 252 observations (about 252 x 1.45 + buffer), and review the 1-sigma threshold.
  Confidence: confirmed (the lookback); the threshold is a policy call.
- Both resizes scale the **whole target position**, but only for names that happen to trade
  that night. Untraded positions keep 100% of their size. REDUCE_GROSS_20 therefore does not
  cut gross by 20%: it shrinks or reverses whichever trades Layer 4 sent. The next night,
  Layer 4 plans the full target again, so the same names are bought back and cut again (see
  the backlog item "Back-and-forth trading").
- On 11-04 this cut the submitted value from $28.5k to $16.1k (-44%) and turned four planned
  opens/adds into closes (Q2).
- Pre-trade `POSITION_SIZE` also rejected 4 trades between 10-30 and 11-03 (MU 5.3%, ARE 5.0%,
  BA 5.1%, BA 5.0%) because the optimiser clamps at exactly `max_position_pct` 0.05
  (`config.yaml:171`), and whole-share rounding pushes the result slightly over the 0.05
  pre-trade limit (`risk/pre_trade.py:168-176`, `config.yaml:206`). Small effect.

**Fix:** see Q2.

### Constraint 3: fills

Of 65 orders from 10-27 to 11-03, 43 filled (66%); 19 expired as CANCELLED and 3 FAILED
(`execution_orders`). By value, $64.3k of $90.3k ordered filled (~71%), and $19.2k of the
filled value was closes or reductions that undid earlier opens. See Q3.1. Filled opening value per night after 10-28 was $3.2-6.7k, and
filled closes were $3.0-7.9k, so the book has stopped growing.

**Confidence: confirmed** (replay matches the log trade list row for row; query results above).

---

## Q2. Executed orders do not match the Layer 4 trade list

### Where "current shares" comes from

- Layer 4: `run_portfolio.py:290` calls `load_positions` (`portfolio/state.py`), which reads
  `portfolio_positions`. The book sync wrote that table at 22:10:26, one second before Layer 4.
  Values are copied into `position_approvals.current_shares` (`portfolio/rebalance.py:225`).
- Layer 5 reuses `current_shares` from the approval row (`risk/pre_trade.py:125`,
  `risk/tail_risk.py:368`).
- Executor: takes the size from the approval's `delta_shares` (`execution/executor.py:190-194`).
  It only falls back to `portfolio_positions` when delta is NULL. It checks live Alpaca
  positions only to refuse a SELL/COVER with no matching position (`:238-242`).
- They agree. The second book sync at 22:10:35, just before execution, reported no changes, and
  `position_approvals.current_shares` matches `portfolio_positions` for every row. The "changed
  AKAM, CLX, TXN" earlier in the night were 11-03's fills (AKAM COVER 4, CLX SHORT 7, TXN SELL 1)
  arriving in the sync. That is correct and already reflected in Layer 4's "Cur" column.

### How the numbers arise (all 16 rows reproduced)

`new_target = round(round(T x 0.5) x 0.8)` for blackout names and `round(T x 0.8)` otherwise,
each applied only while the row is still BUY/SHORT. Python's `round` rounds halves to the nearest
even number.

| Ticker | Cur | L4 target | Blackout | After pre-trade | After tail (x0.8) | Sent | Note |
|---|---|---|---|---|---|---|---|
| KIM | 0 | 192 | no | 192 | 154 | BUY 154 | |
| ARE | -42 | -92 | no | -92 | -74 | SHORT 32 | |
| AVY | 0 | 24 | no | 24 | 19 | BUY 19 | |
| CLX | -21 | -44 | yes (11-02) | -22 (SHORT 1) | -18 | **COVER 3** | flip |
| BA | -14 | -28 | no | -28 | -22 | SHORT 8 | |
| CVNA | -6 | -20 | no | -20 | -16 | SHORT 10 | |
| AIZ | 4 | 16 | yes (11-03) | 8 | 6 | BUY 2 | |
| NKE | -41 | -50 | no | -50 | -40 | **COVER 1** | flip from 0.8 alone |
| COIN | -10 | -17 | no | -17 | -14 | SHORT 4 | |
| ZTS | -31 | -25 | (closing, exempt) | -25 | -25 | COVER 6 | |
| AXON | 0 | -4 | yes (11-05) | -2 | -2 | SHORT 2 | |
| COHR | -2 | -6 | yes (11-04) | -3 (SHORT 1) | -2 | HOLD | add erased |
| AKAM | -10 | -14 | yes (11-05) | -7 (now COVER, exempt from tail) | -7 | **COVER 3** | flip |
| CF | 10 | 13 | yes (11-04) | 6 (now SELL, exempt) | 6 | **SELL 4** | flip |
| MU | 3 | 5 | no | 5 | 4 | BUY 1 | |
| TXN | 11 | 10 | no | 10 | 10 | SELL 1 | |

The final rows match the database exactly (`position_approvals` for 2026-11-04). The 12 rows
tail risk modified are KIM, ARE, AVY, CLX, BA, CVNA, AIZ, NKE, COIN, AXON, COHR and MU, which
matches "modified 12 trades".

### Verdict

- **The executor is correct.** It sends exactly `action` and `|delta_shares|` from the approval
  row. Its direction guard (`execution/executor.py:223-230`) never fired, because
  `map_action` keeps action and delta consistent.
- **"Current shares" is consistent** across Layer 4, Layer 5 and the executor.
- **The wrong component is the Layer 5 resize rule.** Both `risk/pre_trade.py:145` and
  `risk/tail_risk.py:369` multiply the **target position** instead of the **trade**. For an add
  to an existing position (cur -41, target -50), a 20% cut to the target (-40) lands below the
  current holding, so Layer 4's "short 9 more" becomes "cover 1".
- **Design history:** the double cut was first described in session 284a3bba
  (2026-10-30T07:34Z): blackout halves the target, then tail risk takes another 20%. The user
  asked for packet 56 at 07:57Z, which re-derives the action instead of scaling the trade.
  Scaling the delta rather than the target, and the effect on building from an empty book, were
  never discussed. The 11-04 review (f1142635, 2026-11-04T11:56-12:00Z) added the backlog item.
- **Directions do flip, and by design.** Packet 56 (`docs/specs/56-resize-trade-direction.md`,
  Assumptions) states that "a resize that turns an opening trade into a reduction is wanted",
  and `tests/test_risk_tail_risk.py::TestReduceGrossAction::test_cut_below_holding_sells` pins
  that behaviour. The flip is the specified behaviour, not an implementation slip. I think the
  design is wrong for two reasons. First, it only applies to names that trade that night, so it
  neither cuts gross by a consistent 20% nor holds steady. Second, Layer 4 undoes it the next
  night, which produces churn (CLX: SHORT 43, COVER, COVER, COVER, SHORT 7, COVER 3 over six
  nights). This matches the 11-04 backlog entry "Earnings blackout halves the whole target"
  (`docs/backlog.md`).
- Specs involved: `docs/specs/05-risk-management.md` AC3 ("resizes the trade to 50% of target
  shares") and AC5 ("reduces APPROVED non-closure target shares"). The code follows them
  literally. Spec 05 contradicts itself: section line 325 says "full position blocked", and line
  338 says "delta_shares halved".

**Confidence: confirmed.**

**Fix (small to medium):**
- Apply resizes to the **opening delta**, never past the current holding:
  `new_delta = sign(delta) x floor(|delta| x scale)`, `new_target = cur + new_delta`. An add
  can then shrink to zero but never reverses.
- Apply REDUCE_GROSS as a gross scale in Layer 4, scaling the optimiser's gross targets by 0.8,
  so the whole book, not just that night's trades, targets 120%.
- Return `REDUCED` from the blackout path so `run_risk_check.py:144` counts it.

This needs a new spec that supersedes the packet 56 assumption and updates spec 05 AC3/AC5.

---

## Q3. Other findings

### 3.1 Unfilled orders: time-in-force, limit offset, roll-forward

- **TIF:** `TimeInForce.DAY` (`execution/executor.py:107`). **Limit:** close of `score_date`
  ±0.5% (`execution/executor.py:30-33`, `config.yaml:248` `limit_slippage_pct: 0.005`; the
  comment says "mid" but the code uses the close, `:296-305`). Orders submitted at about 22:10
  UTC queue for the next session and expire at its close.
- KIM BUY 80 @ 25.11 (11-03 close 24.99): the 11-04 low was 25.325, above the limit, so no fill.
  ZTS COVER 9 @ 83.40 (close 82.99): the 11-04 low was 84.83, so no fill. Any gap of more than
  0.5% the wrong way leaves the order unfilled. Fills are therefore biased toward names that
  moved in the order's favour (adverse selection, meaning you mostly trade when the move is
  against your signal).
- **Roll-forward:** there is no explicit roll. The next run's order sync marks the expired order
  CANCELLED (`execution/order_sync.py:16`, `:20-29`). Layer 4 then re-plans from the synced book,
  so the intent comes back only if the name still survives the turnover budget and Layer 5 (KIM
  came back on 11-04 as BUY 154). This works, but in practice each unfilled order also spends
  that night's turnover budget.
- Fill rate: 43/65 orders (66%), ~71% of order value, from 10-27 to 11-03. Of the 11-04
  orders, 11 of 15 had filled by 15:13 UTC on 11-05.
- **Design history:** the order type was a deliberate choice. In session 630ff079
  (2026-10-24T16:54Z) the user chose "Queued DAY limits (Recommended)" over "OPG + second cron"
  after learning that Alpaca rejects OPG orders between 9:28am and 7pm New York time. The
  10-21 audit (5978daab, 2026-10-21T13:10Z, item C4) had suggested submitting near the open or
  using OPG/CLS orders. Rolling unfilled orders forward was never discussed.
- **Confidence:** confirmed. **Fix (small):** use OPG (market-on-open) or a limit at the next
  open ± k x ATR, or a wider band. Track the fill rate on the dashboard. **Spec:**
  `docs/specs/06-order-execution.md` / packet 49 chose DAY limits deliberately, so no spec is
  contradicted.

### 3.2 Trading into earnings

- The roughly 40 nightly warnings come from `portfolio/rebalance_schedule.py` (2-day check) and
  are advisory by design: spec 04 AC9 says "never blocks execution".
- New positions near earnings are intended at reduced size. Layer 4 halves the weight
  (`portfolio/optimizer.py:84-89`, `:168-185`; spec 04 AC3; forward window `score_date..+5d`,
  `:421-437`). Pre-trade halves the target again (spec 05 AC3; window ±5 days, so CLX was still
  blacked out on 11-04 after reporting on 11-02). Then reduce_gross applies 0.8. AXON: optimiser
  weight 1.7%, so -4 shares; pre-trade gives -2; tail gives -2; SHORT 2 opened the day before
  earnings.
- So the behaviour is intended, but three stacked haircuts with inconsistent windows look
  accidental. Spec 05 line 325 ("full position blocked") contradicts its own AC3.
- **Confidence:** confirmed. **Fix (small):** pick one owner. Either Layer 4 handles earnings
  (halve, or exclude new opens within N days), or pre-trade blocks *new* opens within N days and
  freezes existing positions. Remove the other path and align the windows.

### 3.3 Tear sheet values

| Symptom | Root cause | Fix |
|---|---|---|
| Inception 2024-01-02 | Hard-coded: `config.yaml:253`, default in `reporting/tear_sheet.py:34` and `run_reporting.py:93`. | Small: set it to 2026-10-27 or derive it from the first trade. |
| SPY annualised 220%, Sharpe 4.5 | `portfolio_nav` still holds a stale **2026-05-21** row (NAV 100,000, SPY 738.98; with matching 05-21 rows in `portfolio_history` and PENDING approvals). The first SPY "daily" return spans 5 months (+3.6%), and `_perf_stats` annualises 8 returns with `252/8` (`reporting/tear_sheet.py:297-301`): `1.0377^31.5 - 1 = 221%`. Annualising 8 days is not meaningful anyway. | Small: delete or exclude rows before inception (a DB delete needs approval). Suppress annualised stats below about 60 observations. |
| Beta 0.00 | Hard-coded `"beta": 0.0` (`reporting/tear_sheet.py:314`). Test: `test_tear_sheet_beta_is_computed`. | Small: regress portfolio returns on SPY returns. |
| Mean slippage -149 bps, P95 -0.9 bps | `slippage_bps` is computed against the **limit** price (`execution/order_sync.py:62-63`). A limit order can only fill at or better than its limit, so every value is ≤ 0. The numbers measure overnight gaps (CVNA -1049 bps, CSGP -667) rather than cost. The mean and P95 are arithmetically fine (P95 is the least favourable value). Test: `test_buy_filled_above_decision_close_is_adverse_slippage`. | Small: store the decision price (the score_date close) on `execution_orders` and measure against it. |
| Factor scores (momentum 23, quality 17) | `reporting/tear_sheet.py:49-53` takes **one arbitrary row** of `factor_scores` (`ORDER BY score_date DESC LIMIT 1`). On 11-04 that row was ZTS (momentum 23.33, quality 16.67, value 78.33), a short. It is not a portfolio exposure. Test: `test_tear_sheet_factor_exposures_come_from_the_book`. | Small: compute the weighted long-short spread over `portfolio_positions` (`portfolio/factor_exposure.py` `compute_exposures` already does this). |
| 30-day turnover vs budget | See Q1: a 30-day metric is compared with a per-run budget. | Small: label it or drop the budget column. |

Spec: `docs/specs/07-reporting-dashboard.md` AC7 requires "factor and sector exposure tables"
and "recent slippage stats". A single ticker's scores and slippage against the limit price both
fail the intent of AC7, though not its literal wording. Beta is not required by AC7, but the
tear sheet prints the field. Confidence: confirmed for each row.

### 3.4 Historical errors

| Date | Error | Root cause | Status |
|---|---|---|---|
| 10-26 | Pipeline failed at Book sync: `int(acct.daytrade_count)` on None (log lines 430-440). `run_all.py:126` stops a real run after a failed sync; 10-26 was still a `--whatif` run, so Layers 4-7 ran in preview. | Alpaca returned a null `daytrade_count`. | **Fixed** by packet 53 (`docs/specs/53-alpaca-null-daytrade-count.md`, commit `b1257b6`, merged 2026-10-27 08:39 UTC; session 9d2a0c48, 2026-10-27T08:27-08:40Z, after which a manual `--sync` recorded the 10-26 NAV). |
| 10-28 | Two CLX COVERs: Layer 4 COVER 21.4 (fractional) plus circuit-breaker `force_close` inserting COVER 43; Alpaca rejected the second ("held_for_orders 43"). | FORCE_CLOSE added a second row and left the original approved. The 3% breaker cap was below the 5% sizing cap. | **Fixed** by packet 54 (`docs/specs/54-breaker-position-trim.md`, commit `cd6eaba`, 2026-10-29): the cap is now 6% and `risk/circuit_breakers.py:403-422` rejects the night's other rows for the ticker and trims to 5% rather than closing. The user chose to cancel the three force-close orders (session ef8503c3, 2026-10-29T08:25Z). Fractional shares were fixed by packet 56. |
| 10-29 | ZTS SHORT / TXN BUY rejected "qty must be > 0". | Resize left delta 0 with the action unchanged; `delta or (...)` treated 0.0 as missing. | **Fixed** by packet 56 (`docs/specs/56-resize-trade-direction.md`, commit `291ca08`, 2026-10-30): resizes re-derive the action, and `execution/executor.py:190-194` and `:223-230` skip zero/HOLD rows and refuse action/delta mismatches. Diagnosed in session 284a3bba (2026-10-30T07:34Z). |

Residual latent risk (likely, low): `_write_approvals` (`portfolio/rebalance.py:215-236`) appends
without clearing earlier rows for the date, and the executor's open-order set is a snapshot taken
once per run (`execution/executor.py:171`). A manual re-run of Layer 4 and Layer 5 for the same
date leaves two APPROVED sets. The Alpaca open-order check only protects while the first run's
orders are still open. Not seen in the logs. Fix: delete or supersede PENDING/APPROVED rows for
the date before inserting.

---

## Recommended order of work

1. New spec: Layer 5 resizes act on the opening delta and never reverse it, and REDUCE_GROSS
   becomes a Layer 4 gross scale (Q2; supersedes packet 56's assumption; updates spec 05
   AC3/AC5).
2. New spec: turnover budget excludes the build-up to target gross, and trimmed names are
   recorded (Q1; updates spec 04 AC7).
3. Execution pricing / TIF to raise the fill rate (Q3.1).
4. One owner for the earnings haircut (Q3.2).
5. Tear sheet fixes (Q3.3). Remove the 2026-05-21 rows (destructive; needs approval).
6. Fix the FRED lookback to use 252 observations, and review whether a credit z above 1σ that
   has stayed there for weeks should keep CAUTION active indefinitely (it fired on 8 of 8 runs).

## Prior design history consulted

| Session | Time (UTC) | What it records |
|---|---|---|
| 5978daab | 2026-10-21T13:10 | Scores audit H4: 30%/day budget means at least 5 days to build 150% gross; suggested exempting the build. C4: DAY limits after the close; suggested near-open or OPG/CLS. |
| 630ff079 | 2026-10-24T15:01 | Credit z 2.74: CAUTION would cut gross 20% in real trading. |
| 630ff079 | 2026-10-24T16:54 | User chose queued DAY limits over OPG + a second cron. |
| 630ff079 | 2026-10-24T17:43 | "About four nights" to build the book; user moved on to the net-exposure fix. |
| 9d2a0c48 | 2026-10-27T08:27-08:42 | 10-26 sync crash diagnosed, packet 53 merged, `--whatif` removed from cron (go-live); four-night estimate repeated. |
| ef8503c3 | 2026-10-29T08:25-08:35 | 3% breaker force-closes and the second CLX order; user cancelled; packet 54. |
| 284a3bba | 2026-10-30T07:34-07:57 | Wrong-way and zero-qty orders traced to blackout x tail-risk resizes; packet 56. |
| f1142635 | 2026-11-04T11:56-12:00 | Run review: blackout x0.8 = 0.4x plan, churn; both added to the backlog (PR #125). |

