# Research Page "Execute All Approved Trades" Button

**Spec file:** `docs/specs/62-execute-button.md`
**Spec slug:** execute-button
**Status:** Done
**Owner:** DTidey
**Date:** 2026-11-06

## Problem statement
`dashboard/page_research.py` `_approval_section` (button "Execute All Approved Trades") marks
the PENDING rows that pass its veto checks APPROVED, then runs `run_execution.py` with no flags.
With no `--execute` or `--dry-run` the script prints its help and exits 0
(`run_execution.py:186-188`), and the page shows "Submitted N orders". Nothing reaches Alpaca.
Found 2026-10-24 (session 3fb357f6); never fixed.

Three more defects sit behind the missing flag:
- **Wrong date.** Even with `--execute`, the default `--date` is today. The rows a daytime
  click approves belong to last night's rebalance (date D); `execute_approvals` selects
  `APPROVED` rows for today only, finds none, and exits 0.
- **Stale rows.** The section lists every PENDING row of any date. The live database holds
  8 PENDING rows from 2026-05-21 (read-only SELECT, 2026-11-06), so the button is on screen now.
  A naive `--date` fix would price limits from a six-month-old close.
- **Double submission.** Nothing records that an approval was executed: rows stay `APPROVED`.
  `execute_approvals` re-selects every `APPROVED` row for the date on every run. The only guard
  is the Alpaca open-order list, which no longer holds an order once it fills or expires. A
  second run for the same date (a second click, a click after the nightly run for that date,
  or a manual rerun of Layer 6) resubmits filled trades.
- **Vetoes keyed on the date.** `_run_veto_checks` reads the ticker as `row[1]`, but
  `sa.select(position_approvals)` returns columns in table order (`id, rebalance_date, ticker,
  action, ...`), so `row[1]` is the rebalance date. Every veto is keyed and displayed as the
  date, any single veto vetoes every row, and the earnings-blackout lookup searches for a ticker
  equal to the date (never matches). Once the button really submits, these vetoes matter.

## Investigation (questions 1-4)

### 1. What the button should run
`[sys.executable, "run_execution.py", "--execute", "--date", D]` with `cwd` = repository root,
where D is the latest `position_approvals.rebalance_date` (the newest nightly rebalance).
- `--execute`: the only mode that submits. `--dry-run` would mislead in the same way.
- `--date D`: approvals and the limit price both key on the rebalance date. Limits come from
  the stored close for D (packet 60: close x (1 +/- 1.5%)). The nightly cron (22:00 UTC, 17:00
  ET, `run_all.py` with no `--date`) writes approvals for D = today; a click the next day must
  name D explicitly. If the close for D is missing for every row, `run_execution.py` already
  exits 1 (packet 60), which the page now reports as a failure.
- `cwd`: `run_execution.py` reads `config.yaml` and `cache/` relative to the working directory;
  the old call relied on the dashboard's working directory.
- **Daytime click submits into the live session.** The page's own veto (check 2) refuses clicks
  outside 09:30-16:00 ET on weekdays, so every click that reaches Layer 6 is during market
  hours. Packets 49/60 queue DAY limits after the close for the next open; a daytime click
  instead sends DAY limits that are live at once: marketable ones fill now, the rest rest until
  16:00 ET and expire. The price reference is the previous close, not the current quote. If the
  name has moved more than 1.5% overnight in the order's favour the order rests and likely
  expires; if against, it fills at the current price (better than the limit). This matches the
  packet 49/60 design (limits from the score date's close); no live quote is added here.
- **When the button appears in practice.** Nightly Layer 5 resolves every PENDING row for D to
  APPROVED or REJECTED before Layer 6 runs, so PENDING rows for D survive only when Layer 5 did
  not run (Layer 4 succeeded, then the pipeline stopped) or when a row was reset by hand with the
  candidate card "Reset" button. The button is a manual recovery path, not the normal route.

### 2. Duplicate submission
Before this packet a double submission was possible:
- `execute_approvals` filters `rebalance_date == D` and `status == 'APPROVED'`; it never changes
  the approval's status after submitting, and never reads `execution_orders`.
- The open-order guard (`_open_order_tickers`) only sees orders still open at Alpaca. A daytime
  click's marketable orders fill within seconds; a second click minutes later sees no open order
  and resubmits.
- Nightly Layer 6 for D+1 cannot pick up the button's D rows (different date), and the button
  cannot run during the nightly (market-hours veto vs the 22:00 UTC cron). But a click on day
  D+1 for D re-selects the rows the D nightly already submitted (they are still `APPROVED` and
  their orders filled at the open), and two browser tabs can run two Layer 6 processes at once.

Fix (no schema change):
- The executor skips an approval when `execution_orders` already holds a row for the same
  `(rebalance_date, ticker, action)` with any status other than `DRY_RUN`. Reason
  `already_submitted`. `FAILED` counts: a submit that raised (timeout) may still have reached
  Alpaca, so a retry is manual. The set is updated after each submission, so two approvals for
  the same key in one run also submit once.
- `run_execution.py --execute` / `--dry-run` hold an exclusive non-blocking lock on
  `cache/execution.lock`; a second process exits 1 with "another execution run holds the lock".
- Status transitions are unchanged: PENDING -> APPROVED (Layer 5 or this button) and
  `execution_orders` is the record of what was sent. No `EXECUTED` status is added.

### 3. Honest reporting
The page reports what Layer 6 did, from the return code and from `execution_orders` rows for D
created since the click:
- exit code != 0: error with the exit code and the stderr tail; any orders recorded before the
  failure are listed.
- exit 0 and no order submitted: warning "No orders were submitted", never a success message.
- exit 0, some submitted and some failed or missing: warning naming the failed and the
  not-submitted tickers.
- exit 0 and every approved ticker has a submitted order: success with the order count.
A table lists each order (ticker, action, shares, status, order id); stdout and stderr tails
are in an expander.

### 4. TRIMMED rows (packet 57 open risk)
- The bulk button cannot touch a `TRIMMED` row: it selects `status == 'PENDING'` and its
  update is guarded by `status == 'PENDING'`; the executor selects only `APPROVED`.
- The candidate card buttons can: `_set_status` updates every row for (date, ticker) with no
  status filter, so "Approve" turns a `TRIMMED` row `APPROVED` and "Reset" turns it `PENDING`
  (then the bulk button picks it up). With this packet such a row is executed by the next click
  for D.
- Spec 57 (edge cases) states this is intended: "a deliberate manual action and then trades".
  A one-line block would contradict spec 57, so it is **not** implemented here. Backlog entry
  "Candidate card status buttons act on every row" records the wider problem (the same buttons
  also overwrite REJECTED and already-submitted rows) for David to decide.

## Scope
In scope:
- `dashboard/execute_approvals.py` (new): latest rebalance date, pending rows for it, stale
  pending count, the approve update, the command, running it, reading back orders, the outcome.
- `dashboard/page_research.py` `_approval_section`: use the module; render the outcome.
- `dashboard/page_research.py` `_run_veto_checks`: read `row.ticker` / `row.action` by name.
- `execution/executor.py`: `already_submitted` skip.
- `run_execution.py`: execution lock.
- `docs/backlog.md`: card status buttons entry.

Out of scope / non-goals:
- Live quotes for daytime limits; an `EXECUTED` approval status; a schema change.
- The logic of the veto checks in `_run_veto_checks` (only the row indexing changes). Known
  issues left as they are: net exposure with signed weights (backlog), and check 8 calls
  `is_shortable(ticker)` with one argument, so it raises, is swallowed, and never vetoes (the
  executor's own `not_shortable` check still applies to SHORT orders).
- Cleaning the 8 stale 2026-05-21 PENDING rows (a DB write; David's decision).
- Candidate card buttons (`_set_status`); see question 4.

## Assumptions
- `position_approvals.rebalance_date` and `execution_orders.rebalance_date` are ISO
  `YYYY-MM-DD` strings; `execution_orders.created_at` is `datetime.now(UTC).isoformat()`, so
  string comparison orders it.
- One dashboard host and one pipeline host share `cache/` (both run from
  `/home/david/hedge/ls_equity_fund`), so a file lock serialises them.

## Proposed behavior / API
```python
# dashboard/execute_approvals.py
latest_rebalance_date(conn) -> str | None
pending_rows(conn, rebalance_date) -> list[Row]
stale_pending_count(conn, rebalance_date) -> int
approve_pending(conn, ids) -> None              # PENDING -> APPROVED only
execution_command(rebalance_date) -> list[str]  # [python, run_execution.py, --execute, --date, D]
run_execution(rebalance_date) -> CompletedProcess   # cwd = repo root, captured output
orders_since(conn, rebalance_date, since) -> list[dict]
outcome(returncode, orders, approved_tickers) -> Outcome(level, message)
```
Executor result for a skipped duplicate:
`{"ticker": t, "action": a, "status": "SKIPPED", "reason": "already_submitted"}`.

## Acceptance criteria
- AC1: The button runs `run_execution.py --execute --date D` with `cwd` at the repository root,
  where D is the latest `position_approvals.rebalance_date`; nothing else is run.
- AC2: The button lists, approves and executes only PENDING rows for D. PENDING rows of older
  dates are counted in a notice and never approved or executed; with no PENDING row for D the
  button is not shown.
- AC3: A success message appears only when the run exits 0 and every ticker approved by the
  click has a submitted order (a row created since the click with an order id and a status other
  than `FAILED`/`DRY_RUN`).
- AC4: A non-zero exit shows an error with the exit code and the stderr tail, and no success.
- AC5: Exit 0 with no submitted order shows a warning, not a success; exit 0 with some orders
  failed or missing shows a warning naming them.
- AC6: The page lists each order recorded since the click (ticker, action, shares, status,
  order id).
- AC7: `execute_approvals` skips an approval with reason `already_submitted` when
  `execution_orders` holds a non-`DRY_RUN` row for the same date, ticker and action (including
  `FILLED`, `CANCELLED` and `FAILED`), and submits no order for it; two approvals with the same
  key in one run submit once.
- AC8: `run_execution.py --execute` exits 1 without calling the executor when another process
  holds `cache/execution.lock`; the lock is released after a normal run.
- AC9: `DRY_RUN` rows do not block a later real submission.
- AC10: `_run_veto_checks` keys vetoes by ticker (not by the rebalance date), so a veto on one
  ticker leaves the other rows approvable.

## Security considerations
- Auth/authz impact: none; the dashboard has no auth (unchanged).
- Input handling or injection risk: the subprocess takes a list (no shell); D comes from the DB
  and is validated with `date.fromisoformat` before use.
- Secrets or credential handling: none. stderr shown on the page is the script's log output,
  which does not print keys (`get_account` logs equity and buying power only).
- Data exposure or privacy impact: none.
- File system access impact: new lock file `cache/execution.lock` (empty).
- Network or external service impact: the button now really submits orders to Alpaca (paper by
  default, `ALPACA_PAPER`). The new skip and lock only reduce submissions.
- Dependency or supply-chain impact: none (`fcntl` is stdlib).
- Security notes for reviewers/testers: confirm no path can submit twice for one approval key.

## Edge cases
- Close missing for D for every row: Layer 6 exits 1 (packet 60); page shows the error.
- Close missing for some rows: exit 0, those tickers listed as not submitted.
- Market closed / halt lock: veto blocks the whole batch before any status change (unchanged).
- Run fails after approving: rows stay `APPROVED` and the button disappears; the message says
  so and names the safe manual rerun (`python run_execution.py --execute --date D`).
- A PENDING row reset by hand after the nightly submitted it: approved again, then skipped as
  `already_submitted`.

## Rollout
1. Merge; restart the `meridian-dashboard` user service so the page reloads the code.
2. Before the first click, reject or delete the 8 PENDING rows from 2026-05-21 (David).
3. No config change. Revert is a code revert; the lock file is inert.

## Verify in paper trading
- After a click during market hours: page shows N orders with Alpaca ids; Alpaca order list
  shows the same N; `execution_orders` rows for D created at the click time.
- A second click (if any PENDING remains) reports no new orders and `already_submitted` in the
  log.

## Test guidance
- Unit: `tests/test_execute_button.py` (helpers, `_approval_section` and `_run_veto_checks`
  with streamlit and `subprocess.run` mocked), `tests/test_execution_executor.py`, `tests/test_run_execution.py`.
- Adversarial: non-zero exit, exit 0 with zero orders, second click while orders are open,
  click with no close for D, stale-date rows, concurrent run, nightly rerun after fills.
- No network: Alpaca client is a `MagicMock`; the subprocess is patched.

## Decision log
- Key the duplicate guard on `(rebalance_date, ticker, action)` in `execution_orders` rather
  than a new approval status: no schema or migration, and it also covers manual and nightly
  reruns. `execution_orders` has no approval id column.
- Count `FAILED` as submitted: a submit exception may hide an order Alpaca accepted.
- File lock rather than a DB lock: one host, stdlib only, and covers the window between the
  executor's duplicate check and its insert.
- Latest rebalance date, not "previous trading day": the page cannot know the calendar; a
  missing close fails loudly in Layer 6.
