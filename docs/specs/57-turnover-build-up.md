# Turnover Budget Excludes the Build-up to Target Gross

**Spec file:** `docs/specs/57-turnover-build-up.md`
**Spec slug:** turnover-build-up
**Status:** Draft (phase 1: awaiting owner decisions D1-D3 before implementation)
**Owner:** DTidey
**Date:** 2026-11-05

## Problem statement
The paper book has stayed at about 27-31% gross since 2026-10-29 against a 150% target
(`portfolio.target_long_gross` 0.80 + `target_short_gross` 0.70). The investigation
(`docs/investigations/2026-11-05-underinvested-book.md`, PR #126, finding 1) shows that the
per-run turnover budget is the main cap:

- `portfolio/rebalance.py` `_apply_turnover_budget` sums `|delta_shares| x price` for this run's
  trades. When that exceeds `turnover_budget_pct` (0.30) of NAV it keeps closures, splits the
  remaining budget between the long and short books by trade value, fills each book greedily by
  `|combined_score - 50|` (skipping a trade that does not fit), and sets everything else to
  `HOLD` with `delta_shares = 0`. `_write_approvals` drops HOLD rows, so a trimmed name leaves no
  trace in `position_approvals`.
- Nothing distinguishes building the book from cash from rotating a full book. On 2026-11-04
  the book was 26.8% gross ($26,663 / $99,601 NAV); the 40-name target needed trades worth
  $124.8k (125.3% of NAV). The budget kept 16 trades worth 28.6% and set 24 to HOLD, 22 of them
  names not yet held (new longs CSX, AWK, LLY, NDSN, ABBV, RVTY, ACGL, KEYS, SWKS, FOXA, ALLE,
  AMGN; new shorts DVA, MOS, ABT, LII, PEG, LVS, BX, HII, MCO, ETN).
- The greedy skip favours small top-ups of held names over large opens of new names: AXON
  (conviction 45.3, $1.7k) was kept, DVA (47.3, $5.0k) and MOS (47.0, $4.6k) were trimmed.
  12 of the 16 kept trades were changes to names already held.
- Reproduction: `test_build_from_cash_not_capped_by_turnover_budget` in the investigation
  branch (a 40-name, 150% target from an empty book produces 6 trades, not 40).

Spec drift: spec 04 AC7 and its section on the turnover budget (line 432) say trades are
trimmed by "smallest |delta score| first"; the code ranks by `|combined_score - 50|` and skips
trades that do not fit. Spec 04 never considers the build-up.

Goal: the budget limits how much of an existing book is rotated per night, not how fast the
book moves to its target gross. A trimmed trade is visible in the run output and the database.

## Design choices (owner decision needed)

### D1. How the build-up is excluded
- **Option A (recommended): additive build-up allowance per book.** Each book's budget becomes
  its share of the rotation budget plus the gap between its target gross and current gross in
  dollars: `allowance_book = |target_gross_book - current_gross_book|` (both valued at the
  run's prices). Opening up to target is free; rotation inside a book still spends the 30%.
  The gap is taken as an absolute value, so a book that must shrink (for example to a lower
  gross target under packet 58) can de-risk without spending the rotation budget either.
  Trade-off: on a night like 11-04 the whole book (about 125% of NAV) is sent at once, on one
  night's signals. At $100k NAV market impact is negligible (orders are a few $k against
  S&P 500 liquidity).
- Option B: `max(budget, |target_gross - current_gross|)`. Smaller change, but any rotation on
  a build-up night competes with the build for the same allowance, so the book still needs
  extra nights when names also rotate. Less predictable.
- Option C: a separate ramp-up budget (for example `build_up_budget_pct: 0.60`) used while the
  book is below `0.8 x` target gross. Gives a deliberate 2-3 night ramp and limits how much is
  committed on one night's scores, at the cost of a new config key and a mode switch with a
  threshold edge.
- Option D: a turnover budget measured over N runs (for example 5 x 30% rolling). Allows a big
  first night, but needs turnover history (from `execution_orders` fills or approvals) and
  still caps a later re-build after a de-risking event. Most code; not recommended.

Recommendation: A. It matches the budget's documented intent ("max fraction of portfolio
turned per rebalance", `config.yaml`), is a few lines inside the existing per-book split, and
handles de-risking symmetrically. If the owner wants a slower ramp, C can be added later on top
of A as a cap on the allowance.

### D2. What happens to trades that do not fit
- **Option A (recommended): keep the greedy skip, record trimmed rows.** Trimmed trades are
  written to `position_approvals` with `status = 'TRIMMED'` and their original action and
  delta, and the run prints the count and value trimmed. Nothing downstream reads `TRIMMED`
  (pre-trade reads `PENDING`; tail risk, breakers and the executor read `APPROVED`), so no
  order results. The skip is kept because, once the build-up is free, the budget only binds on
  rotation, where filling the budget fully is the better use of it.
- Option B: stop at the first trade that does not fit (strict conviction order). Simpler to
  explain, but leaves budget unused.
- Option C: log only, no database rows. No schema use, but the dashboard and later reviews
  cannot see what was trimmed.

### D3. Spec 04 AC7 ranking text
- **Recommended:** restate spec 04 AC7 to match the code (rank by `|combined_score - 50|`,
  skip trades that do not fit). Ranking by `|delta score|` would need the previous night's
  scores per name, which `generate_trades` does not receive; no evidence the old wording was
  ever implemented.

## Scope
In scope:
- `portfolio/rebalance.py` `_apply_turnover_budget`: per-book build-up allowance (D1); mark
  trimmed rows (D2).
- `portfolio/rebalance.py` `_write_approvals`: write trimmed rows with status `TRIMMED`.
- `run_portfolio.py`: print the turnover summary (needed, allowance, rotation budget, kept,
  trimmed count and value).
- `docs/specs/04-portfolio-construction.md`: AC7 and the turnover-budget section restated;
  pointer to this packet.
- `config.yaml`: comment on `turnover_budget_pct` updated (value unchanged).
- Move the investigation's `test_build_from_cash_not_capped_by_turnover_budget` repro into
  `tests/test_rebalance.py` without its xfail marker (only if PR #126 has merged; otherwise an
  equivalent test is written here).

Out of scope / non-goals:
- Layer 5 cuts (packet 58), credit z-score (packet 59), fill rate (packet 60).
- The 30-day turnover printed next to the per-run budget in the tear sheet (investigation
  finding 6, reporting).
- Hysteresis or minimum holding period for back-and-forth trades (backlog item).
- Changing `turnover_budget_pct` (stays 0.30).

## Assumptions
- `price` in `generate_trades` (the score date's close) is the right valuation for the gap,
  as it already values every trade in the budget.
- A book's gross is measured from signed shares: long book = positions with shares > 0, short
  book = positions with shares < 0; the target side comes from `target_shares` sign.
- A trade that crosses zero (long to short) does not exist today (packet 56 non-goal); if one
  appears it is valued once in the book of its target direction, as now.

## Proposed behavior / API
### Public interface
- `generate_trades(...)` signature unchanged. The returned frame gains no columns; trimmed
  rows keep `action = HOLD`, `delta_shares = 0` in the returned frame (as today) so callers are
  unaffected, and carry their original action/delta into a new internal column used only by
  `_write_approvals`.
- `position_approvals.status` gains the value `TRIMMED` (string column; no schema change).

### Inputs / outputs
- Per book `b` in {LONG, SHORT}:
  - `cur_gross_b = sum(|current_shares x price|)` over current positions on side `b`;
  - `tgt_gross_b = sum(|target_shares x price|)` over targets on side `b`;
  - `allowance_b = |tgt_gross_b - cur_gross_b|`;
  - `book_budget_b = rotation_share_b + allowance_b`, where `rotation_share_b` is today's
    proportional split of `budget x NAV - closure value`.
- The budget is only applied when `proposed_turnover > budget + (allowance_L + allowance_S) / NAV`;
  otherwise every trade passes, as today when under budget.
- Error handling: unchanged (NAV <= 0 keeps today's behaviour of no trimming).

### Examples
```python
# 2026-11-04 shape: book 26.8% gross, target 150%, trades needed 125.3% of NAV.
# Before: 16 trades kept (28.6%), 24 HOLD, no record.
# After (D1 option A): allowance ~123% + rotation 30% >= 125.3% -> all 40 trades kept.
# Full book, pure rotation of 50% of NAV, gross unchanged: allowance ~0 -> 30% kept, rest TRIMMED.
```

## Acceptance criteria
- AC1: From an empty book, a 40-name target at 150% gross with a 30% budget produces 40
  non-HOLD trades (none trimmed).
- AC2: For a full book at target gross, a rotation needing more than the budget keeps trades
  worth at most `turnover_budget_pct x NAV` (closures excepted) and trims the rest, as today.
- AC3: A partly built book (current gross below target) keeps trades worth up to the rotation
  budget plus the per-book gap; the long book's allowance cannot be spent by the short book or
  vice versa.
- AC4: A book whose target gross is below its current gross (de-risking) can reduce to target
  without the reductions being trimmed by the rotation budget.
- AC5: Full closures are never trimmed and still spend the rotation budget first (unchanged).
- AC6: Every trimmed trade is written to `position_approvals` with `status = 'TRIMMED'`, its
  original `action`, `target_shares` and `delta_shares`; no other stage reads or acts on
  `TRIMMED` rows, and the executor submits no order for them.
- AC7: `run_portfolio.py` prints the turnover summary: trade value needed, build-up allowance,
  rotation budget, kept value and count, trimmed value and count (as % of NAV and $).
- AC8: With `commit=False` (what-if) no rows are written, including `TRIMMED` rows.
- AC9: Spec 04 AC7 and its turnover-budget section describe the shipped behaviour (conviction
  ranking, skip-if-not-fit, build-up allowance, `TRIMMED` rows).

## Security considerations
- Auth/authz impact: none.
- Input handling or injection risk: none; inputs are our own tables and config.
- Secrets or credential handling: none.
- Data exposure or privacy impact: none.
- File system access impact: none.
- Network or external service impact: more and larger Alpaca orders on build-up nights (about
  125% of NAV in one night from a 27% book). Every order still passes the Layer 5 pre-trade
  veto (position size, sector, gross <= 1.65, net, beta, liquidity) and the circuit breakers;
  no limit is loosened.
- Dependency or supply-chain impact: none.
- Security notes for reviewers/testers: the change only enlarges what Layer 4 proposes on
  nights when the book is below (or above) target gross; check that the allowance can never
  exceed the actual gap, so a full book cannot use it to rotate faster.

## Edge cases
- Empty target (all closures): allowance on each side equals its current gross; everything
  passes (closures were never trimmed anyway).
- One book at target, the other empty: only the empty book gets an allowance.
- Price missing (0.0) for a ticker: its value is 0 in both the gap and the trade value, as
  today; it neither spends nor creates allowance.
- NAV from config vs broker: `nav_usd` is the value used today; unchanged.
- Allowance exactly equal to the remaining trade value: boundary trade is kept (`<=`).

## Rollout
1. Land after or with packet 58 (recommended order: 59, 58, 57, 60; see
   `log/changelog-2026-11-05.md`). Without 58, a full-size build hits the blackout x0.5 and
   reduce-gross x0.8 target cuts on far more rows, including direction flips.
2. First night: run `python run_portfolio.py --whatif` by hand before the cron run and check
   the turnover summary (expect about 40 trades, ~120-125% of NAV at a 27% book).
3. No config change; revert is a code revert (no schema change; `TRIMMED` rows are inert).

## Verify in paper trading
- Night 1: `position_approvals` for the score date holds ~40 rows (APPROVED + REJECTED +
  TRIMMED); Layer 6 log shows ~35-40 orders.
- Day 2-3: Alpaca gross (book sync `portfolio_positions`) reaches at least 100% within two
  nights of the change, allowing for fills (packet 60) and Layer 5 (packet 58).
- Steady state: on nights when the book is within 5% of target gross, `TRIMMED` rows appear
  only when rotation exceeds 30%.
- Read-only check: `SELECT status, count(*), sum(abs(delta_shares)) FROM position_approvals
  WHERE rebalance_date = :d GROUP BY status`.

## Test guidance
- AC1 -> `tests/test_rebalance.py::TestBuildUpAllowance::test_build_from_cash_not_trimmed`
- AC2 -> `TestBuildUpAllowance::test_full_book_rotation_still_capped`
- AC3 -> `TestBuildUpAllowance::test_partial_book_gap_per_side`,
  `test_long_gap_not_spent_by_short_book`
- AC4 -> `TestBuildUpAllowance::test_de_risking_reductions_not_trimmed`
- AC5 -> existing `TestTurnoverBudget::test_closures_are_never_trimmed`
- AC6 -> `tests/test_rebalance.py::TestTrimmedRows::test_trimmed_rows_written`,
  `tests/test_risk_pre_trade.py::TestTrimmedIgnored::test_trimmed_not_evaluated`,
  `tests/test_execution_executor.py::TestTrimmedIgnored::test_no_order_for_trimmed`
- AC7 -> `tests/test_run_portfolio.py::test_turnover_summary_printed` (new module; no
  `run_portfolio` test module exists yet)
- AC8 -> `TestTrimmedRows::test_whatif_writes_nothing`
- AC9 -> review of `docs/specs/04-portfolio-construction.md` diff (documentation AC)

## Decision log
- 2026-11-05: Spec written from the 2026-11-05 investigation. The 2026-10-21 scores audit
  (H4) already said a 30% per-run budget means at least five nights to build 150% gross and
  suggested exempting the initial build; the "about four nights" estimate accepted at go-live
  (sessions 630ff079 2026-10-24, 9d2a0c48 2026-10-27) assumed every budgeted trade would be
  sent and filled in full. This packet does not reverse an owner decision: the owner accepted
  a slow build as a consequence, not as a goal.
