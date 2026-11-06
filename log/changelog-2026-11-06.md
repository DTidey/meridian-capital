# Changelog 2026-11-06

## Packet 62: execute button

- **Date for the button is the latest `position_approvals.rebalance_date`, not "today" and not
  the previous trading day.** Approvals and limit prices both key on the rebalance date; the
  page has no market calendar, and a missing close already fails Layer 6 loudly (packet 60).
  PENDING rows of older dates are counted, never executed: the live DB holds 8 from 2026-05-21
  whose close is six months old.
- **Duplicate guard in `execution_orders`, not a new approval status.** Keyed on
  `(rebalance_date, ticker, action)`. No schema change, and it also covers a manual Layer 6
  rerun and the nightly. `execution_orders` has no approval id, so the key is the closest
  available identity; two approvals with the same key in one run now submit once.
- **`FAILED` blocks a retry.** `submit_order` catches every exception (including a timeout
  after Alpaca accepted) and writes `FAILED`, so `FAILED` does not prove nothing was sent.
  Retrying is a manual decision. `DRY_RUN` rows do not block.
- **File lock in `run_execution.py`.** The button can only run during market hours and the
  nightly runs at 22:00 UTC, so they do not overlap; two browser tabs can. `fcntl.flock` on
  `cache/execution.lock` closes the window between the duplicate check and the insert.
  Dashboard and cron run from the same checkout, so they share `cache/`.
- **TRIMMED not blocked.** The bulk button never touches TRIMMED rows. The candidate card
  buttons can, but spec 57 states that is a deliberate manual action. Backlog entry instead.
- **Veto index fix included.** `_run_veto_checks` used `row[1]` (the rebalance date) as the
  ticker. Out of the original ask, but once the button really submits, the vetoes it relies on
  must work; it is a by-name read of the same columns. Check 8's broken `is_shortable` call is
  left for the backlog (the executor checks shortability itself).
