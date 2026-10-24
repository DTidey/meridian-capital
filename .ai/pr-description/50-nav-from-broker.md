## Summary

Fixes data audit C11: NAV was never marked to market, so the circuit breakers could not fire.
Third of three packets agreed on 2026-10-24: 44 book of record, 49 order handling (both
merged), 50.

- The nightly book sync records Alpaca account equity (cash plus positions, so realised and
  unrealised P&L and costs) in `portfolio_nav`, in the same transaction as the positions.
- `build_nav_series` no longer overwrites NAV with config `nav_usd` plus history P&L; it only
  fills SPY closes and drawdown.
- Circuit breakers: daily and weekly P&L are returns on the previous recorded NAVs, drawdown is
  from the highest recorded NAV; no NAV for the day skips the P&L checks with a warning.
- `get_nav(config, conn, score_date)` returns the latest recorded NAV (config `nav_usd` before
  the first sync); `run_portfolio.py` and `run_risk_check.py` size, check and stress-test with it.
- Turnover comes from filled orders (any final status), not market-value changes between
  snapshots, which counted price drift once snapshots became nightly.
- Specs 05 and 07 annotated; audit C11 fix line; turnover backlog item removed.

## Spec

- Spec: `docs/specs/50-nav-from-broker.md`
- Test plan: `docs/test-plans/50-nav-from-broker.md`
- PR draft path: `.ai/pr-description/50-nav-from-broker.md`

## Acceptance Criteria

- [x] AC1: The book sync records Alpaca equity as the date's NAV (same transaction; re-sync
  replaces; dry run writes nothing).
- [x] AC2: `build_nav_series` never changes NAV; fills drawdown and SPY.
- [x] AC3: Breakers use returns on previous recorded NAVs and the recorded peak; a losing day
  moves drawdown; no NAV today means no trigger and a warning.
- [x] AC4: `get_nav` returns the latest recorded NAV or the config fallback; runners use it.
- [x] AC5: Turnover from filled order value; price drift does not count.

## Security Review

- [x] Security considerations reviewed in `docs/specs/50-nav-from-broker.md` (no new inputs or
  calls; breakers now see real P&L)

## Validation

- `make lint`: pass
- `make test`: see PR checks
- `make security`: pass
- 11 of the circuit-breaker and turnover tests fail against `main`
- Read-only production check: recorded NAV 100,000; no NAV yet for 2026-10-23, so the breakers
  warn and skip; the first NAV row comes from the 2026-10-26 nightly sync

Reviewer pass (fresh subagent): no blockers. Important items fixed: a recorded NAV of zero or
below is no longer treated as missing (a wiped-out account trips the kill switch); the
same-transaction test now fails at commit, after the NAV insert. Suggestions applied: the sync
upserts only `nav`/`computed_at` (keeps reporting's drawdown on a manual re-sync); a test for a
NAV exactly 7 days back.

## Risks and follow-ups

- Cash deposits or withdrawals would read as P&L (paper account only).
- The first new NAV row (2026-10-26) follows the 2026-05-21 row, so its P&L attribution spans
  about five months; harmless with a flat $100,000 book.
- The dashboard's weekly P&L uses the NAV five recorded rows back, the breakers the latest NAV at
  least 7 calendar days back; they can differ after a missed sync (pre-existing).
- Execution metrics that count only FILLED orders remain in the backlog.
- C11 is signed off once two nightly NAV rows exist and drawdown is seen moving on a losing day
  (needs positions, so after the first real paper rebalance).
