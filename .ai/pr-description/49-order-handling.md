## Summary

Fixes data audit C12: orders were marked CANCELLED in the database after a 120-second poll
while staying live at Alpaca, which filled them the next morning unrecorded. Second of three
packets: 44 book of record (merged), 49 (this), 50 NAV (C11).

- Orders stay DAY limits from the nightly run, which Alpaca queues for the next session (owner's
  choice: Alpaca rejects opening-auction OPG orders submitted 9:28am-7:00pm New York time, when
  the nightly run executes).
- No polling: each order's row keeps Alpaca's status at submission (normally `PENDING`).
- New `execution/order_sync.py` `sync_orders`: every open row is updated from Alpaca (fills,
  average price, slippage against the order's limit, final status). `CANCELLED` only when
  Alpaca reports canceled/expired; partial fills are kept. Runs inside `run_execution --sync`
  (the nightly "Book sync" step) before the book sync, and before `--execute` / `--dry-run`.
- Limits from the stored close for the score date (not a stale position price or
  `adj_close`); no close for that date means no order.
- No order for a ticker with an open Alpaca order; open orders are listed at Alpaca's maximum
  page size and a full (possibly truncated) page, or a failed listing, means nothing is
  submitted.
- `--execute` exits 1 if every approval was skipped for lack of a close (e.g. a wrong date).
- `poll_timeout_s` / `poll_interval_s` removed from config; spec 06 annotated; audit C12 fix line
  and Verify amended (no live quote exists after the close).

## Spec

- Spec: `docs/specs/49-order-handling.md`
- Test plan: `docs/test-plans/49-order-handling.md`
- PR draft path: `.ai/pr-description/49-order-handling.md`

## Acceptance Criteria

- [x] AC1: No polling or sleeping; the executor never writes `CANCELLED`; rows hold the Alpaca
  order id and mapped status.
- [x] AC2: Limits from the score date's close +/- the buffer; no close means skipped, no order.
- [x] AC3: No order for a ticker with an open Alpaca order; raises before submitting if open
  orders cannot be listed.
- [x] AC4: `sync_orders` maps Alpaca status, records fills and slippage, never refetches final
  rows.
- [x] AC5: `--sync` syncs orders before the book; `--execute` / `--dry-run` sync orders first.
- [x] AC6: A failed fetch or unreadable order leaves that row; the others still sync.
- [x] AC7: `--execute` exits 1 when every approval lacked a close.

## Security Review

- [x] Security considerations reviewed in `docs/specs/49-order-handling.md` (no new order paths;
  duplicate-order guard fails closed)

## Validation

- `make lint`: pass
- `make test`: see PR checks
- `make security`: pass
- Against `main`, the no-close and open-order tests fail; the polling path waits out the
  120-second poll per order (the defect)

Reviewer pass (fresh subagent): one blocker fixed (the open-orders listing defaulted to 50
orders, so the duplicate guard could miss tickers; now 500 and a full page fails closed).
Important items fixed: `suspended` stays open; unreadable order values are isolated per row;
an all-`no_close` run fails. Deferred to the backlog: FILLED-only execution metrics,
`PENDING` rows without an order id after a crash mid-submit, slippage against the limit.

## Risks and follow-ups

- Nightly runs are `--whatif`, so nothing is submitted until the cron runs without it. The first
  real paper rebalance then allows the C10/H5/C12 sign-offs (orders queued one night, fills and
  positions recorded the next).
- Partial fills that expire count as `CANCELLED` (with `filled_shares`), so they are excluded
  from the FILLED-only slippage stats.
