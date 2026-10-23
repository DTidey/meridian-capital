# Factor Monitor Baseline

**Spec file:** `docs/specs/46-factor-monitor-baseline.md`
**Spec slug:** factor-monitor-baseline
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-23

## Problem statement
The Layer 5 factor monitor (`risk/factor_monitor.py`) raises 8 MEDIUM `FACTOR_SPREAD` alerts
every night with meaningless z-scores. The 2026-10-22 nightly run reported value_score
z = -19,861, quality_score z = -7,946, and so on. The logs show the same pattern back to at least
2026-10-16. Two defects combine:

1. **It scores an empty book.** Nightly runs use `--whatif`, so `portfolio_positions` is empty
   (0 rows on 2026-10-23). `compute_exposures` fills an empty book with 50.0 for every factor,
   so every spread is 0. The guard `if not today_spread` never fires because the dict is
   non-empty.
2. **The baseline is a different quantity.** Today's value is the portfolio's weighted
   long-minus-short spread. The history it is compared with is, per score date, the mean of the
   universe's top half minus the mean of its bottom half (`_compute_universe_spread`). On
   percentile scores that is about 50 points with almost no day-to-day variance. Any portfolio
   spread therefore lands thousands of standard deviations away. Spec 05 section 8 step 2
   describes this baseline, so the spec is wrong too.

Effect: the alerts are pure noise. A real HIGH alert (wide spread on a crowded factor, spec 05
AC8) cannot be told apart from the noise.

Goal: the monitor stays silent when there is no book. It z-scores today's portfolio spread
against past portfolio spreads computed the same way.

## Scope
In scope:
- `risk/factor_monitor.py`:
  - skip when the book has no LONG or no SHORT positions;
  - replace the universe top-half/bottom-half baseline with past portfolio spreads from
    `portfolio_history`.
- `docs/specs/05-risk-management.md` section 8: correct the algorithm text.
- New tests: `tests/test_risk_factor_monitor.py` (no factor monitor tests exist today).

Out of scope / non-goals:
- Making `portfolio_positions` and `portfolio_history` track a real book (packet 44, audit C10).
- Layer 5 not seeing the Layer 4 what-if book. In `--whatif` mode the factor risk model,
  correlation monitor and factor monitor all read an empty `portfolio_positions`. This needs its
  own decision and goes in the backlog.
- `portfolio/factor_exposure.py` `flag_unusual_exposures` / `_historical_spreads`: never called,
  and broken (it pools all dates into one value). Left untouched; noted in the backlog.
- Alert threshold, HIGH/MEDIUM promotion rule, `risk_log` format for alerts.

## Assumptions
- Past portfolio spreads use the same formula as today's spread: `compute_exposures` on that
  snapshot's `ticker`, `direction`, `weight`, joined to `factor_scores` for the same date.
  `_weighted_avg` uses absolute weights, so signed or unsigned short weights give the same
  result.
- The history window stays at the last 252 snapshot dates before `score_date`, and the minimum
  stays at 10 dates (`_MIN_HIST_DATES`).
- Until about 10 trading days after packet 44 starts writing real snapshots, the monitor
  reports insufficient history and raises no alerts. This is accepted: no book, no factor risk.
- Depends on packet 44 for correct history. Today `portfolio/rebalance.py`
  `_update_positions` passes only new and adjusted tickers to `save_positions`, so a
  `portfolio_history` date holds that day's trades, not the book that stood. Packet 44 must
  write the full book on every commit; until then the baseline is not like-for-like. No harm
  today: production has one snapshot date, so the monitor skips.
- Layer 4 can commit more than once a day. Each commit stamps all its rows with one
  `recorded_at`, so the rows carrying a date's latest `recorded_at` are its last commit.

## Proposed behavior / API
### Public interface
- Unchanged signature: `run_factor_monitor(conn, positions_df, score_date, config, whatif=False)
  -> list[dict]`.
- Removed private helpers: `_compute_universe_spread`, `_load_252_score_dates`,
  `_MIN_TICKERS_PER_HALF`; also the unreachable `if not today_spread` guard.
- New private helper `_load_history_spreads(conn, score_date)`: loads past portfolio spreads
  from `portfolio_history` and `factor_scores`. It returns `{factor: [spread, ...]}`, one value
  per usable snapshot date, in three queries (dates, snapshots, scores).

### Inputs / outputs
- Inputs: `positions_df` (current book), `portfolio_history`, `factor_scores`, `crowding_flags`.
- Outputs: alert list as today, `{"type": "FACTOR_SPREAD", "factor", "z", "priority"}`.
- Skip paths: each returns `[]`. When not `whatif`, each writes one `risk_log` row with
  `check_type="factor_monitor"`, `result="OK"` and a reason:
  - `no positions` — `positions_df` has no LONG or no SHORT rows;
  - `no factor scores for <date>` — unchanged;
  - `insufficient history for z-scoring (<n> snapshot dates)` — fewer than 10 usable dates.
- The no-positions and insufficient-history paths log one INFO line (not WARNING) naming the
  reason; both are expected until a real book has 10 snapshots. Missing factor scores for
  today stays a WARNING, since it means Layer 2 did not run.

### Examples
```text
# 2026-10-22 nightly (--whatif, empty portfolio_positions), after this packet:
--- Factor Monitor ---
  (no alerts)
INFO risk.factor_monitor — factor_monitor: no positions — skipped
```

## Acceptance criteria
- AC1: With no LONG or no SHORT positions, the monitor returns `[]` and does not query history.
  When not `whatif`, it writes one `risk_log` row `OK` / `no positions`.
- AC2: The baseline for each factor is the list of weighted long-minus-short spreads, one per
  distinct `portfolio_history.snapshot_date` strictly before `score_date`, from the last 252
  such dates. Each is computed with `compute_exposures` against `factor_scores` of the same
  date.
  - A snapshot date with no `factor_scores` rows is skipped.
  - A snapshot date with no LONG or no SHORT rows is skipped, as today's book is (AC1).
  - A date with more than one commit: only the rows with that date's latest `recorded_at`
    are used, so a ticker closed between commits does not carry over.
- AC3: With fewer than 10 usable snapshot dates, the monitor returns `[]` and raises no alert.
  When not `whatif`, it writes one `risk_log` row `OK` naming the count.
- AC4: z = (today's spread - mean(baseline)) / sample std(baseline). Today's spread equal to the
  baseline mean gives z = 0. A spread 2 sample std above the mean alerts at threshold 1.5. A
  factor with std < 1e-9 is skipped, as today.
- AC5: Alert priority and `risk_log` alert rows are unchanged. A factor with |z| above threshold
  and in a flagged crowding pair is HIGH, otherwise MEDIUM.
- AC6: Regression on the 2026-10-22 shape: empty `portfolio_positions` and 117 score dates of
  `factor_scores` give zero alerts.
- AC7: `docs/specs/05-risk-management.md` section 8 describes the portfolio-history baseline
  and the no-positions skip.
- AC8: `make lint`, `make test` and `make security` pass.

## Security considerations
- Auth/authz: no change.
- Input handling or injection risk: reads only, through SQLAlchemy Core; no user input.
- Secrets or credential handling: no change.
- Data exposure or privacy impact: none; no new output fields.
- File system access impact: none.
- Network or external service impact: none.
- Dependency or supply-chain impact: none.
- Security notes for reviewers/testers: `risk_log` writes stay behind `not whatif`.

## Edge cases
- A one-sided book (shorts all closed): skipped as no positions, because a spread against the
  50.0 default is not meaningful.
- A ticker in a past snapshot with no `factor_scores` row that day: scored 50.0 by
  `_weighted_avg`, the same as today's calculation.
- A snapshot written for `score_date` itself (Layer 4 committed earlier in the run): excluded,
  so today is not part of its own baseline.
- The single 2026-05-21 snapshot in production: 1 date, so insufficient history.
- A factor column missing from `factor_scores`: no spread for it; it is skipped.
- A `portfolio_history` row with null `weight`: absolute weight 0 in `_weighted_avg`. If all
  weights on a side are 0, that side defaults to 50.0, as `compute_exposures` does today. An
  all-null weight column is converted to numeric first, so it does not crash.
- Null `recorded_at`: sorts before any timestamp; if a date's rows are all null, they are all
  used.

## Test guidance
Uses an in-memory DB with `portfolio_history`, `factor_scores` and `crowding_flags` inserts, the
same pattern as `tests/test_risk_circuit_breakers.py`.
- AC1 -> `tests/test_risk_factor_monitor.py::TestNoPositions` (empty, long-only, short-only;
  whatif writes nothing)
- AC2 -> `tests/test_risk_factor_monitor.py::TestBaseline` (same-date join, missing scores date
  skipped, one-sided date skipped, two commits, closed ticker between commits, today excluded,
  252 cap, null weights)
- AC3 -> `tests/test_risk_factor_monitor.py::TestInsufficientHistory` (9 dates vs 10)
- AC4 -> `tests/test_risk_factor_monitor.py::TestZScore` (z = 0 at mean, alert at +2 std,
  constant history skipped)
- AC5 -> `tests/test_risk_factor_monitor.py::TestPriority`
- AC6 -> `tests/test_risk_factor_monitor.py::TestEmptyBookRegression`
- AC7 -> review of the spec 05 diff
- AC8 -> `make lint`, `make test`, `make security`

## Production verification (after merge)
1. On the next nightly run, `logs/run_all_<date>.log` shows `factor_monitor: no positions —
   skipped` and no `FACTOR_SPREAD` alerts.
2. After packet 44 is live and 10 snapshots exist, z-scores are within single digits.

## Decision log
- 2026-10-23: Baseline from past portfolio spreads in `portfolio_history`, not from a proxy built
  from the scored LONG/SHORT candidates. The candidate proxy has 117 dates today, but it measures
  about 100 equal-weighted candidates per side, not the 20/20 weighted book. Its level would
  differ systematically and bias every z-score. Waiting for real history is quieter and correct.
- 2026-10-23 (review): A date's last commit, not the latest row per ticker. With full-book
  snapshots the per-ticker rule lets a ticker closed by a second same-day commit leak back in
  from the first.
- 2026-10-23 (review): The baseline's dependency on packet 44 writing full-book snapshots is
  recorded here and on audit item C10, since the current writer stores only the day's trades.
- 2026-10-23: Skip a one-sided book instead of scoring it against the 50.0 default; the default
  makes the spread an artefact.
