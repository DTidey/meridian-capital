# Order Handling: Queued DAY Orders and Order Status Sync

**Spec file:** `docs/specs/49-order-handling.md`
**Spec slug:** order-handling
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-24

## Problem statement
Data audit item C12 (`docs/data-audit-2026-10-21.md`): orders are marked cancelled in the
database but left live at Alpaca.

The nightly pipeline reaches Layer 6 at about 22:30 UK, after the US close. `execute_approvals`
submits DAY limit orders, polls each for up to 120 seconds, and on timeout writes `CANCELLED`
(and `filled_shares = 0`) to `execution_orders` without cancelling anything at Alpaca. Alpaca
queues DAY orders submitted after the close for the next session, so they fill the next day
and nothing records it. Limits come from `portfolio_positions.current_price` or, failing that,
the latest `adj_close` (which is dividend-adjusted, not a tradable price).

Owner's decisions (2026-10-24): orders stay DAY limits submitted by the nightly run and queued
by Alpaca for the next session (Alpaca rejects opening-auction OPG orders submitted between
9:28am and 7:00pm New York time, which rules them out for a 22:00 UK run). The next night's
run records what happened. Second of three packets: 44 (book of record, merged), 49 (this),
50 (NAV, C11).

Goal: `execution_orders` always shows Alpaca's view of each order. An order is final in the
database only when Alpaca says it is final.

## Scope
In scope:
- `execution/executor.py`:
  - `poll_order` removed; `execute_approvals` records Alpaca's status at submission (open
    orders stay `PENDING`) instead of waiting;
  - limits from the stored `close` for `score_date`; an approval with no close for that date
    is skipped;
  - no order for a ticker that already has an open order at Alpaca.
- New `execution/order_sync.py`: `sync_orders(conn, client)` updates every open
  `execution_orders` row from Alpaca (status, filled shares, average fill price, slippage).
- `run_execution.py`: `--sync` runs `sync_orders` then `sync_book`; `--execute` and
  `--dry-run` sync orders before executing.
- `config.yaml`: `poll_timeout_s` and `poll_interval_s` removed.
- Docs: spec 06 note, audit C12 fix line and Verify wording, CHANGELOG, decision log.

Out of scope / non-goals:
- Order type, timing and schedule (owner's decision above); chunking by ADV (unchanged).
- NAV and turnover (packet 50).
- Cancelling or replacing orders automatically; `--cancel-pending` and the Ctrl-C handler stay
  as they are, and the next sync records the cancellation.
- The 8 stale `PENDING` approvals from 2026-05-21 (`execute_approvals` only reads `APPROVED`
  rows for `score_date`).

## Assumptions
- Alpaca order statuses (alpaca-py `OrderStatus`): `filled`; `partially_filled`; final without
  a full fill: `canceled`, `expired`, `replaced`; failed: `rejected`; every other status
  (`new`, `accepted`, `pending_new`, `held`, `done_for_day`, `pending_cancel`, `suspended`,
  ...) is still open. `done_for_day` is treated as open: a DAY order then moves to `expired`.
  `suspended` is open because Alpaca may still release the order. `replaced` ends the row; the
  replacement order is not tracked (nothing in the pipeline replaces orders).
- Database statuses keep their current names so the dashboard and `--status` keep working:
  open = `PENDING` (no fill yet) or `PARTIAL` (some fill, still working); final = `FILLED`,
  `CANCELLED` (cancelled or expired; `filled_shares` keeps any partial fill), `FAILED`.
- Slippage is measured against the order's own `limit_price` as Alpaca reports it.
- The nightly run's `score_date` is the US session that just closed, so its stored `close` is
  the latest tradable price.

## Proposed behavior / API
### Public interface
- `execution.executor.execute_approvals(conn, client, score_date, config, cache_dir,
  dry_run=False)`: same signature; returns one result per approval with `status` `SUBMITTED`,
  `SKIPPED` (with `reason`), `FAILED` or `DRY_RUN`.
- `execution.order_sync.sync_orders(conn, client) -> dict`:
  `{"checked": n, "updated": n, "final": n}`.
- `execution.order_sync.db_status(alpaca_status, filled_qty) -> str`: the mapping above.

### Inputs / outputs
- Submission: one `execution_orders` row per chunk, with the Alpaca order id and the mapped
  status of the order Alpaca returns (normally `PENDING`).
- Skips (no order, no row): no close for `score_date` (`reason="no_close"`); an open order for
  the ticker at Alpaca (`"open_order"`); existing reasons (`not_shortable`,
  `no_alpaca_position`).
- Sync: for each row in `PENDING`/`PARTIAL` with an order id, fetch the order and update
  `status`, `filled_shares`, `avg_fill_price`, `slippage_bps`, `updated_at`.
- Error handling: failing to list open orders before submitting raises (nothing is submitted
  when duplicates cannot be ruled out). A failed fetch for one order in `sync_orders` is logged
  and leaves that row unchanged.

## Acceptance criteria
- AC1: `execute_approvals` does not poll or sleep, and never writes `CANCELLED`; a submitted
  order's row holds the Alpaca order id and the mapped Alpaca status (`PENDING` while open).
- AC2: Limits are `close` for `score_date` +/- `limit_slippage_pct`; an approval whose ticker
  has no close on `score_date` is skipped with `reason="no_close"` and no order.
- AC3: No order is submitted for a ticker with an open Alpaca order (`reason="open_order"`).
  Open orders are listed with Alpaca's maximum page size (500); if they cannot be listed, or a
  full page comes back (possibly truncated), `execute_approvals` raises before submitting
  anything.
- AC4: `sync_orders` updates every open row from Alpaca: fully filled -> `FILLED`; partly filled
  and working -> `PARTIAL`; canceled/expired -> `CANCELLED` with any partial fill kept;
  rejected -> `FAILED`; still open -> unchanged status. Fill price and slippage (against the
  order's limit) are recorded. Final rows are never fetched again.
- AC5: `run_execution.py --sync` runs `sync_orders` before `sync_book`, and `--execute` /
  `--dry-run` run `sync_orders` before executing.
- AC6: A failed Alpaca fetch, or an order whose values cannot be read, leaves that row as it
  was and does not stop the sync of the others.
- AC7: `run_execution.py --execute` exits 1 when every approval was skipped for lack of a close
  (`no_close`), so a wrong date (e.g. a run that crosses UK midnight) does not pass silently.

## Security considerations
- Auth/authz, secrets: unchanged (Alpaca credentials from `.env`).
- Network: one Alpaca read per open order per sync and one open-orders listing per execution
  run; no new order-placing paths.
- Trading safety: refusing to submit when open orders cannot be listed prevents duplicate
  orders; orders are never reported cancelled while live.
- Input handling: Alpaca values parsed with `float()`; statuses mapped through a fixed table.
- Dependencies, file system: no change.

## Edge cases
- Alpaca rejects at submission: the row is `FAILED` immediately.
- Partial fill, then expiry at the close: `CANCELLED` with `filled_shares` > 0 (visible in the
  dashboard's failed/cancelled view); `sync_book` records the actual position.
- An order cancelled with `--cancel-pending`: the next sync records `CANCELLED`.
- Rows from before this packet with no order id: not fetched.
- Weekend or holiday `score_date` with no close: every approval skipped (`no_close`).
- Dry run: no open-orders check, no Alpaca orders; rows marked `DRY_RUN` as today.

## Test guidance
- AC1 -> `tests/test_execution_executor.py::TestSubmission::`
  `test_records_alpaca_status_without_polling`,
  `test_rejected_at_submission_is_failed`
- AC2 -> `TestSubmission::test_limit_from_close_for_score_date`,
  `test_no_close_for_date_skipped`
- AC3 -> `TestSubmission::test_open_order_skipped`, `test_open_orders_unavailable_raises`,
  `test_open_orders_listed_with_max_limit`, `test_truncated_open_orders_raises`
- AC4 -> `tests/test_order_sync.py::TestSyncOrders::*`, `TestDbStatus::*`
- AC5 -> `tests/test_run_execution.py::*`
- AC6 -> `tests/test_order_sync.py::TestSyncOrders::test_fetch_error_leaves_row`,
  `test_malformed_order_leaves_row`
- AC7 -> `tests/test_run_execution.py::test_all_no_close_fails_the_run`,
  `test_some_no_close_still_succeeds`

## Decision log
- 2026-10-24: Queued DAY limit orders from the nightly run (owner's choice over OPG with a
  second 00:30 UK cron); Alpaca rejects OPG orders submitted 9:28am-7:00pm New York time.
- 2026-10-24: Record, do not wait: after the close nothing fills within seconds, so polling only
  produced false cancellations. The next night's sync records the outcome.
- 2026-10-24: Keep the existing status names so the dashboard and `--status` need no change;
  a partially filled order that expires is `CANCELLED` with its `filled_shares`.
- 2026-10-24: Limits from the stored `close`, not `adj_close` (dividend-adjusted) or the
  position's last synced price; skip rather than trade on an older close.
- 2026-10-24 (review): Alpaca returns 50 open orders unless asked for more, so the guard asks
  for 500 and refuses a full page; `suspended` stays open; an all-`no_close` run fails (AC7).
  Deferred to the backlog: FILLED-only execution metrics, a row left `PENDING` without an order
  id if the process dies mid-submit, and slippage measured against the limit.
