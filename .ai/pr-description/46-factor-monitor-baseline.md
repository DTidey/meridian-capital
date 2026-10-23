## Summary

The Layer 5 factor monitor raised 8 MEDIUM `FACTOR_SPREAD` alerts on every nightly run, with
z-scores up to about -20,000 (value_score z = -19,861 on 2026-10-22). Two defects:

- Nightly runs use `--whatif`, so `portfolio_positions` is empty. `compute_exposures` filled both
  books with 50.0, giving a spread of 0 for every factor, and the empty-spread guard never fired.
- The baseline was a universe top-half minus bottom-half spread, about 50 points with almost no
  day-to-day variance, so any book spread landed thousands of standard deviations away.

Now:

- No alerts unless the book has both a LONG and a SHORT position (`risk_log` OK `no positions`).
- Today's spread is z-scored against the book's own past spreads: one per `portfolio_history`
  snapshot date (last 252), computed with `compute_exposures` against that day's scores.
  With fewer than 10 usable dates the monitor skips. Production has 1 (2026-05-21), so it stays
  quiet until packet 44 records a real book.
- Spec 05 section 8 corrected. Two related items added to the backlog: Layer 5 does not see the
  what-if book, and the unused `flag_unusual_exposures` in `portfolio/factor_exposure.py`.

## Spec

- Spec: `docs/specs/46-factor-monitor-baseline.md`
- Test plan: `docs/test-plans/46-factor-monitor-baseline.md`
- PR draft path: `.ai/pr-description/46-factor-monitor-baseline.md`

## Acceptance Criteria

- [x] AC1: No LONG or no SHORT positions: no alerts, no history query, `risk_log` OK `no positions`.
- [x] AC2: Baseline is the book spread per past `portfolio_history` snapshot date (last 252),
  skipping dates without scores or without both books; only a date's last commit is used.
- [x] AC3: Fewer than 10 usable dates: no alerts, `risk_log` OK naming the count.
- [x] AC4: z against the baseline mean and sample std; constant history skipped.
- [x] AC5: Priority and `risk_log` alert rows unchanged.
- [x] AC6: Empty book with 117 score dates gives zero alerts.
- [x] AC7: Spec 05 section 8 describes the new baseline and skip.
- [x] AC8: `make lint`, `make test`, `make security` pass.

## Security Review

- [x] Security considerations reviewed in `docs/specs/46-factor-monitor-baseline.md` (reads only
  through SQLAlchemy Core; `risk_log` writes stay behind `not whatif`; no new inputs or
  dependencies)

## Validation

- `make lint`: pass
- `make test`: 1375 passed
- `make security`: pass ("No known vulnerabilities found")
- The AC6 regression test fails on `main` with 8 MEDIUM alerts.
- What-if run of the new monitor against production for 2026-10-22: 0 positions, no alerts,
  `factor_monitor: no positions — skipped`; 1 usable history date.

Reviewer pass (fresh subagent): no blockers. Important items fixed: an all-null weight column
crashed `abs()`; a date's last commit replaces per-ticker dedup so closed tickers do not leak;
the dependency on packet 44 writing full-book snapshots is now in the spec and on audit C10.
Suggestions applied: the bare-frame test now exercises the no-positions guard; scores are
grouped once per load. Not applied: splitting `run_factor_monitor` (still over 50 lines, as
before this packet), to keep the change surgical.

## Risks and follow-ups

- `portfolio_history` holds only each day's trades until packet 44 writes full books; the
  baseline depends on that (recorded on audit C10). No effect today: one snapshot date.
- The monitor gives no signal until about 10 trading days after packet 44 starts writing real
  snapshots. Intended: there is no book to monitor before then.
- Layer 5 in what-if mode still runs every check on an empty book (backlog item).
