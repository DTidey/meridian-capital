## Summary

The research page button "Execute All Approved Trades" approved PENDING rows, ran
`run_execution.py` with no flags (help text, exit 0), and showed "Submitted N orders". Nothing
reached Alpaca.

- Button runs `run_execution.py --execute --date D` (D = latest rebalance date, `cwd` = repo
  root), only for PENDING rows of D. Older PENDING rows (8 from 2026-05-21 in the live DB) are
  counted, never executed.
- Page reports what happened: exit code, stderr tail, and the `execution_orders` rows written
  since the click. Success only when every approved ticker has a submitted order.
- Executor skips `already_submitted` approvals (a non-`DRY_RUN` `execution_orders` row for the
  same date, ticker and action), so a second click or a rerun cannot resubmit a filled order.
- `run_execution.py --execute` / `--dry-run` take `cache/execution.lock`; a concurrent run exits 1.
- `_run_veto_checks` read the ticker as `row[1]` (the rebalance date): vetoes were keyed on the
  date, one veto blocked every row, and the earnings blackout never matched. Now by name.

## Spec

- Spec: `docs/specs/62-execute-button.md`
- Test plan: `docs/test-plans/62-execute-button.md`
- PR draft path: `.ai/pr-description/62-execute-button.md`

## Acceptance Criteria

- [x] AC1: Button runs `run_execution.py --execute --date D` from the repo root, D = latest rebalance date.
- [x] AC2: Only PENDING rows for D are listed, approved and executed; older ones are counted only.
- [x] AC3: Success only on exit 0 with every approved ticker submitted.
- [x] AC4: Non-zero exit shows an error with exit code and stderr tail.
- [x] AC5: Exit 0 with nothing submitted, or with failed/missing tickers, is a warning.
- [x] AC6: Orders recorded since the click are listed.
- [x] AC7: Executor skips `already_submitted` approvals; one submission per key per run.
- [x] AC8: A second concurrent `--execute` exits 1 while the lock is held; lock released after.
- [x] AC9: `DRY_RUN` rows do not block a real submission.
- [x] AC10: Vetoes are keyed by ticker, not by the rebalance date.

## Security Review

- [x] Security considerations reviewed in `docs/specs/62-execute-button.md`
- Subprocess takes a fixed argv with a validated ISO date, no shell. The button now really sends
  orders to Alpaca (paper by default); the duplicate skip and lock only reduce submissions.

## Validation

- New tests failed before the change (import error for the page module; 8 executor/lock
  failures) and pass after.
- `make lint`, `make test` (1698 passed), `make security` (Bandit clean, pip-audit no known
  vulnerabilities): pass.
- `.github/scripts/validate_pr.py` passes locally with this draft as the PR body.
- Read-only checks (2026-11-06, SELECT in a READ ONLY transaction): PENDING rows exist only
  for 2026-05-21 (8); nightly approvals 10-27..11-05 are all APPROVED/REJECTED; 2026-11-05
  orders are queued PENDING at Alpaca.

## GitHub Checks

- Required checks for `main`: `CI / test`, `CodeQL / analyze`

## Changelog

- [x] `CHANGELOG.md` under `## Unreleased`.

## Open Risks

- Daytime clicks send DAY limits priced from the previous close (+/- 1.5%) into the live
  session; a name that gapped in the order's favour by more than the band rests and expires.
- If the run fails after approving, rows stay APPROVED and the button disappears; the page shows
  the manual rerun command (safe under the new skip).
- `FAILED` rows block automatic retries for that key and date (deliberate: a timeout may hide
  an accepted order).
- Candidate card buttons still set any status on any row, including `TRIMMED` (spec 57 allows
  this); backlog entry added.
- Restart `meridian-dashboard` after merge; reject the 8 stale 2026-05-21 PENDING rows.
