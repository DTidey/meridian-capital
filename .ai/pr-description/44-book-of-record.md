## Summary

Fixes data audit C10 (position tracking cannot follow a real book) and H5 (BRK-B vs BRK.B).
First of three packets agreed on 2026-10-24: 44 book of record, 49 order handling (C12),
50 NAV (C11).

- Alpaca is the book of record. New `execution/book_sync.py` `sync_book` rebuilds
  `portfolio_positions` from the broker's positions with signed shares, market value and
  weight (shorts negative), entry/current price, P&L, sector, latest combined score and beta,
  and replaces that date's `portfolio_history` rows with a whole-book snapshot. One
  transaction; dry-run mode writes nothing.
- `run_all.py` runs it (`run_execution.py --sync`) between Layers 3 and 4 every night, in
  `--whatif` too; a failure stops a real run before Layer 4. `run_execution.py --execute`
  syncs before and after executing.
- Rebalance writes approvals only (`_update_positions` and `save_positions` removed); the
  executor no longer writes positions, so it can no longer delete shorts or stale rows;
  `reconcile_positions` removed.
- `execution/symbols.py`: BRK-B is sent to Alpaca as BRK.B (orders, shortability) and mapped
  back for broker positions.
- Tear sheet sector net weight is long + short now that short weights are negative.
- Pre-trade relied on rebalance having written the target book into positions; it now builds
  the proposed book itself (broker book + PENDING targets), so its sector, gross/net and beta
  checks still see the whole rebalance. This exposed that the correlation check compared a new
  ticker with itself; fixed.
- Specs 04 and 06 annotated; audit C10/H5 fix lines, C11 -> packet 50, C12 -> packet 49.

## Spec

- Spec: `docs/specs/44-book-of-record.md`
- Test plan: `docs/test-plans/44-book-of-record.md`
- PR draft path: `.ai/pr-description/44-book-of-record.md`

## Acceptance Criteria

- [x] AC1: After a sync, `portfolio_positions` is exactly the broker book with signed values and
  full metadata; rows not at the broker are removed.
- [x] AC2: The date's `portfolio_history` rows are a whole book, replaced on re-sync.
- [x] AC3: `entry_date` kept while held in the same direction; reset when new or flipped.
- [x] AC4: Dry run writes nothing.
- [x] AC5: Rebalance commit writes approvals only.
- [x] AC6: The executor never writes positions; Alpaca symbols for orders, shortability and the
  closing guard.
- [x] AC7: `run_all.py` syncs between Layers 3 and 4 (also `--whatif`), skips with
  `--no-execution`, stops a real run on failure.
- [x] AC8: Tear sheet sector net = long + short.
- [x] AC9: Pre-trade checks each trade against the whole proposed book; no self-correlation;
  NULL weight counts as 0.

## Security Review

- [x] Security considerations reviewed in `docs/specs/44-book-of-record.md` (no new
  credentials or order paths; one transaction so a failure keeps the previous book)

## Validation

- `make lint`: pass
- `make test`: see PR checks
- `make security`: pass
- 12 of the updated tests fail against `main`'s source; the pre-trade tests are guarded by
  mutation (broker-book pro-forma, no self-exclusion)

Reviewer pass (fresh subagent): no blockers. Important item 1 (pre-trade saw only the broker
book plus one trade) fixed as AC9. Items 2-4 are pre-existing or reporting-only and go to the
backlog: FIFO attribution with signed shorts and closures, turnover from daily snapshots
(packet 50), the research page's net exposure. Suggestions applied: COVER/SELL guard takes the
sign from the side, execution summary printed before the post-execution sync, unused
`fill_prices` removed, docstring notes the commit, extra sync tests.
- Dry-run sync against the real paper account and production database: 0 positions, no
  differences (account flat at $100,000)

## Risks and follow-ups

- Layer 5's factor risk model, correlation and factor monitors now report on the broker book
  (what is held) instead of the target book.
- From the first nightly run after merge, every run reads Alpaca before Layer 4; an Alpaca
  outage now fails the nightly `--whatif` run (Layers 4-7 still run) instead of passing.
- The 8 stale PENDING approvals from 2026-05-21 are untouched (packet 49 decides order
  handling).
- C10 and H5 are signed off after packet 49 allows a paper rebalance, execute and sync.
