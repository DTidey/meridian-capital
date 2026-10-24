# Book of Record: Positions Synced From Alpaca

**Spec file:** `docs/specs/44-book-of-record.md`
**Spec slug:** book-of-record
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-24

## Problem statement
Data audit items C10 and H5 (`docs/data-audit-2026-10-21.md`). `portfolio_positions` cannot
follow a real book:

- `portfolio/rebalance.py` `_update_positions` writes the target book as positions when
  approvals are created, before Layer 5 has reviewed them or any order has filled.
- `execution/broker.py` `reconcile_positions` overwrites rows with unsigned share counts, while
  Layer 4 (`generate_trades`, the optimiser) treats shares as signed, so the next rebalance
  reads a short as a long. Rows it inserts have no sector, weight, price or score.
- `execution/executor.py` deletes any position whose post-fill share count is <= 0.5, which
  removes every short, and deletes rows when a closing order finds no broker position.
- `portfolio_history` gets rows only for tickers a rebalance touched, so a snapshot date is
  not a whole book. The packet 46 factor monitor reads snapshots as whole books.
- H5: Yahoo and the database spell Berkshire `BRK-B`; Alpaca spells it `BRK.B`. Orders,
  shortability checks and broker positions all use the raw ticker.

Goal: Alpaca is the book of record. Positions are always the broker's positions, with signed
shares and full metadata, and each sync writes a whole-book snapshot. This is the first of
three packets agreed on 2026-10-24: 44 (this, C10 + H5), 49 (order handling, C12), 50 (NAV and
mark-to-market, C11).

## Scope
In scope:
- New `execution/symbols.py`: `to_alpaca(ticker)`, `from_alpaca(symbol)`.
- New `execution/book_sync.py` `sync_book(conn, client, score_date, dry_run=False)`: replaces
  `portfolio_positions` with the broker's positions and writes that date's
  `portfolio_history` snapshot.
- `execution/broker.py`: `get_broker_positions` returns database tickers;
  `reconcile_positions` is removed (replaced by `sync_book`).
- `execution/executor.py`: Alpaca symbols on orders and on the broker-position guard; it no
  longer writes `portfolio_positions` (a sync after execution records fills).
- `execution/short_check.py`: asks Alpaca for the Alpaca symbol.
- `run_execution.py`: `--sync` and the sync before `--execute` / `--dry-run` use `sync_book`;
  a real `--execute` syncs again afterwards.
- `run_all.py`: a "Book sync" step (`run_execution.py --sync`) after Layer 3 and before Layer
  4, also in `--whatif` runs; skipped with `--no-execution`.
- `portfolio/rebalance.py`: `_update_positions` removed; rebalance writes approvals only.
  `portfolio/state.py` `save_positions` removed (no remaining caller).
- `reporting/tear_sheet.py`: sector net weight = long + short (weights are signed).
- `risk/pre_trade.py`: checks 5-8 use the current book with every PENDING target applied
  (before, rebalance had written the target book into `portfolio_positions`, which these
  checks relied on); check 8 skips the new ticker itself; a NULL weight counts as 0.
- Docs: spec 04 and spec 06 notes, audit C10/H5 fix lines, CHANGELOG, decision log.

Out of scope / non-goals:
- Order types, timing, limits, polling and cancels (packet 49, C12). The executor's order
  flow is unchanged apart from symbols and not writing positions.
- NAV, `get_nav`, `portfolio_nav`, circuit-breaker NAV (packet 50, C11).
- H4 (target book versus the pre-trade net limit).

## Assumptions
- Signed convention, as Layer 4 already uses: `shares`, `market_value` and `weight` are
  negative for shorts; `direction` is `LONG`/`SHORT`. Readers checked: pre-trade, circuit
  breakers, factor risk model, factor monitor, dashboard and reports either take `abs()` or
  expect signed values; only the tear sheet's net sector weight needs changing.
- Alpaca positions give `symbol`, `qty`, `side`, `avg_entry_price`, `current_price`. The sign
  is taken from `side` (qty may be reported unsigned), as `get_broker_positions` does today.
  `market_value` and `unrealized_pnl` are computed from signed shares rather than read, so
  their sign never depends on the API.
- `weight` = market value / Alpaca account equity at sync time. Packet 50 replaces equity with
  the recorded NAV if they differ.
- Sector comes from `sp500_universe.gics_sector`, `combined_score` from the latest
  `combined_scores` row on or before `score_date`, beta from `portfolio.beta.compute_betas`
  (60 days, 1.0 when history is short), as Layer 4 does.
- `entry_date` is kept from the existing row while the position keeps its direction; a new
  position or a flipped direction gets `score_date`.

## Proposed behavior / API
### Public interface
- `execution.symbols.to_alpaca("BRK-B") == "BRK.B"`; `from_alpaca("BRK.B") == "BRK-B"`;
  tickers without a class separator pass through unchanged.
- `execution.book_sync.sync_book(conn, client, score_date, dry_run=False) -> dict`
  returning `{"positions": n, "added": [...], "removed": [...], "changed": [...]}`.
- `run_execution.py --sync [--date D]`: sync and print the summary.
- `run_all.py`: new step "Book sync" between Layers 3 and 4.

### Inputs / outputs
- Inputs: Alpaca positions and account equity; `sp500_universe`, `combined_scores`,
  `daily_prices` (beta, and the close when Alpaca gives no current price).
- Outputs (not dry run), in one transaction:
  - `portfolio_positions` holds exactly the broker's positions (rows not at the broker are
    deleted);
  - `portfolio_history` rows for `score_date` are replaced by one row per position.
- Dry run: logs the differences and writes nothing.
- Error handling: an Alpaca error propagates (the caller fails); nothing is written. In
  `run_all.py` a failed sync stops the pipeline before Layer 4 unless `--whatif`, so a real
  rebalance never trades against a stale book.

### Examples
```python
sync_book(conn, client, "2026-10-26")
# broker: AAPL long 10, BRK.B short 5
# portfolio_positions: AAPL shares 10 LONG; BRK-B shares -5 SHORT, market_value < 0
```

## Acceptance criteria
- AC1: After `sync_book`, `portfolio_positions` holds exactly the broker's positions: database
  tickers, signed shares (shorts negative), `direction`, `entry_price`, `current_price`,
  signed `market_value` and `weight`, `unrealized_pnl`, `sector`, `combined_score`, `beta`.
  Rows for tickers not at the broker are removed.
- AC2: `sync_book` replaces the `portfolio_history` rows for `score_date` with one row per
  broker position (a whole book); a second sync on the same date does not duplicate them.
- AC3: `entry_date` is kept for a position held in the same direction and set to
  `score_date` for a new or flipped position.
- AC4: `dry_run=True` reports the same differences and writes nothing.
- AC5: `generate_trades(..., commit=True)` writes approvals only and leaves
  `portfolio_positions` and `portfolio_history` untouched.
- AC6: `execute_approvals` never writes `portfolio_positions`; orders and the shortability
  check use the Alpaca symbol (`BRK.B`), and the closing-order guard matches broker positions
  by database ticker.
- AC7: `run_all.py` runs `run_execution.py --sync` after Layer 3 and before Layer 4, in
  `--whatif` runs too, skips it with `--no-execution`, and stops before Layer 4 when it fails
  unless `--whatif`.
- AC8: The tear sheet's sector net weight is long weight + short weight.
- AC9: Pre-trade checks each PENDING trade against the broker book with every PENDING target
  for the date applied (so, e.g., the net-exposure check sees the whole proposed book); the
  pairwise-correlation check does not compare a new ticker with itself; a position with a NULL
  weight counts as 0.

## Security considerations
- Auth/authz, secrets: unchanged; Alpaca credentials stay in `.env` via `get_client`.
- Network: one extra read of Alpaca positions and account per nightly run (already done by
  `--status`/`--execute` paths); no new endpoints that place orders.
- Data integrity: the sync deletes and rewrites `portfolio_positions` from broker data in one
  transaction, so a failure leaves the previous book intact.
- Input handling: broker values are converted with `float()`; symbols are mapped, not
  interpolated into SQL.
- Risk control: pre-trade keeps checking the whole proposed book (AC9), so moving positions to
  the broker book does not weaken the veto.
- Dependencies, file system: no change.

## Edge cases
- No broker positions: `portfolio_positions` emptied; no history rows for the date.
- A broker symbol outside the universe (e.g. a manual trade): kept, with `sector` and
  `combined_score` NULL and beta 1.0.
- Alpaca gives no `current_price`: the latest stored close is used; if none, the entry price.
- A position flips from long to short between syncs: new `entry_date`, signed shares.
- Account equity of 0: `weight` NULL rather than a division error.
- Share-class symbols other than BRK.B (e.g. BF.B): mapped by the same rule.

## Test guidance
- AC1 -> `tests/test_book_sync.py::TestSyncBook::test_positions_match_broker`,
  `test_short_is_signed`, `test_stale_rows_removed`, `test_metadata_filled`,
  `test_missing_price_falls_back_to_close`
- AC2 -> `TestSyncBook::test_history_is_whole_book`, `test_resync_same_date_replaces_history`
- AC3 -> `TestSyncBook::test_entry_date_kept_for_held_position`,
  `test_entry_date_reset_on_flip`
- AC4 -> `TestSyncBook::test_dry_run_writes_nothing`
- AC5 -> `tests/test_rebalance.py::TestCommit::test_commit_writes_approvals_only`
- AC6 -> `tests/test_execution_executor.py::TestBookOfRecord::test_order_uses_alpaca_symbol`,
  `test_closing_guard_matches_alpaca_symbol`, `test_stale_close_skipped_without_touching_positions`,
  `TestExecuteApprovalsFilled::test_filled_order_leaves_positions_to_book_sync`,
  `tests/test_execution_short_check.py::test_uses_alpaca_symbol`,
  `tests/test_execution_broker.py::TestGetBrokerPositions::test_alpaca_symbol_mapped_to_db_ticker`,
  `tests/test_symbols.py::test_round_trip`
- AC7 -> `tests/test_run_all.py::test_book_sync_runs_between_layers_3_and_4`,
  `test_book_sync_command_and_whatif`, `test_book_sync_skipped_without_execution`,
  `test_book_sync_failure_stops_real_run`, `test_book_sync_failure_continues_whatif`
- AC8 -> `tests/test_tear_sheet.py::test_sector_net_weight_signed`
- AC9 -> `tests/test_risk_pre_trade.py::TestTargetBook::test_net_check_sees_whole_proposed_book`,
  `test_offsetting_shorts_count_toward_net`, `test_new_position_not_correlated_with_itself`,
  `test_null_position_weight_does_not_crash`

## Decision log
- 2026-10-24: Alpaca is the book of record, and the sync runs in `--whatif` runs too (owner's
  choices): reading the broker is bookkeeping, not a trading decision.
- 2026-10-24: Three packets, 44 first, because order handling (49) and NAV (50) build on a
  correct book (owner's choice).
- 2026-10-24: Replace rather than reconcile: positions are rebuilt from the broker each time
  instead of patching discrepancies, so there is one writer and no drift to repair.
- 2026-10-24: Signed shares and weights, matching what Layer 4 and the risk model already
  assume; only the tear sheet needed adjusting.
- 2026-10-24 (review): pre-trade relied on rebalance having written the target book into
  `portfolio_positions`. It now builds that book itself (broker book + PENDING targets), which
  keeps the checks' meaning without a second writer. That also exposed that check 8 compared a
  new ticker with itself (every new position with 20+ days of prices would be rejected).
- 2026-10-24 (review): `run_risk_check.py`'s factor risk model, correlation and factor
  monitors now report on the broker book instead of the target book; that is the book that is
  actually held. Deferred to the backlog: FIFO attribution with signed shorts and closures,
  turnover from daily snapshots (packet 50), the research page's net exposure, and the
  pre-packet-44 partial snapshot in the factor monitor baseline.
