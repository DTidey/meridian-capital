# Backlog

Small items deferred for bundling into a future packet. When an item is
picked up, give it a spec in `docs/specs/` and remove it from this list.

## Position attribution with signed shorts

- **Raised:** 2026-10-24, packet 44 review.
- **Problem:** `reporting/position_attribution.py` (FIFO lots, around lines 159-196) compares
  share counts as if unsigned: a short growing from -5 to -8 takes the "shrank" branch and books
  a closed trade with the wrong sign. Whole-book snapshots never hold a zero row, so a position
  that closes is never closed in FIFO. Pre-existing (target books were already signed); packet
  44's daily snapshots make it visible. `tests/test_reporting_fifo.py` still uses unsigned
  shorts.
- **Fix:** work in absolute shares per direction, and close remaining lots when a ticker drops
  out of the next snapshot; or build lots from `execution_orders` fills instead.

## Execution metrics count only FILLED orders

- **Raised:** 2026-10-24, packet 49 review.
- **Problem:** `dashboard/page_execution.py` (filled orders, slippage, worst fills, daily
  notional) and `execution/costs.py` `slippage_stats` filter on `status == "FILLED"`. With
  queued DAY limits, an order that partly fills and then expires is `CANCELLED` with
  `filled_shares` > 0 and is left out of every metric. Slippage is measured against the order's
  own limit, which a limit order cannot breach, so it is almost always <= 0 bps; measuring
  against the score date's close would say more.
- **Fix:** count `filled_shares` > 0 in any final status; slippage against the stored close.
  Natural fit with packet 50 (NAV and turnover from fills).

## Order rows left PENDING after a crash mid-submit

- **Raised:** 2026-10-24, packet 49 review.
- **Problem:** `execution/executor.py` `_submit_chunk` inserts a `PENDING` row before calling
  Alpaca. If the process dies between the two, the row has no order id: `sync_orders` never
  touches it and it shows as open in `--status` and the dashboard.
- **Fix:** mark rows still `PENDING` without an order id as `FAILED` at the start of the next
  sync, or insert the row only after Alpaca answers.

## Turnover from daily snapshots

- **Raised:** 2026-10-24, packet 44 review.
- **Problem:** `reporting/turnover.py` diffs `market_value` per ticker across
  `portfolio_history` dates. With a snapshot every night, price drift counts as turnover, and
  entries and exits count as nothing.
- **Fix:** packet 50 (NAV): turnover from filled `execution_orders` value over NAV.

## Research page net exposure with signed weights

- **Raised:** 2026-10-24, packet 44 review.
- **Problem:** `dashboard/page_research.py` around line 363 negates the weight of SHORT rows,
  turning signed (already negative) short weights positive; around line 356 `abs(None)` fails
  when a weight is NULL. Pre-existing.
- **Fix:** sum signed weights; treat NULL as 0.

## Factor monitor baseline: pre-packet-44 snapshot

- **Raised:** 2026-10-24, packet 44 review.
- **Problem:** `portfolio_history` holds one partial target-book snapshot from 2026-05-21,
  written before packet 44. The factor monitor baseline (`risk/factor_monitor.py`) treats every
  snapshot date as a whole book. One date out of the 10 required has little effect.
- **Fix:** ignore snapshots before the first packet 44 sync, or delete the 2026-05-21 rows (with
  the owner's go-ahead).

## Risk factors still not found for six 10-Ks

- **Raised:** 2026-10-15, packet 33 universe check.
- **Problem:** C, GE, NEM and SYF use layouts the extractor does not recognise (no Item 1A
  heading, or "Risk Factors Relating to ..." sub-sections only). USB and WFC incorporate their
  risk factors by reference from the annual report exhibit (EX-13), which the analyzer does
  not fetch. All six fall back to the stored 10-K text.
- **Fix:** for USB/WFC, fetch the EX-13 exhibit when Item 1A points to it; for the others,
  inspect their layouts individually.

## Balance-sheet share history

- **Raised:** 2026-10-22, packet 42.
- **Problem:** packet 42 takes market cap from Yahoo's `marketCap`, but
  `fundamentals.shares_outstanding` is still stored as Yahoo reports it. It still feeds Piotroski's
  dilution signal and the market cap fallback. BRK-B is the Class A equivalent (1.43M), CME's
  2026-06-30 row is 1000x too small, and rows before 2025-06-30 often hold issued, not outstanding,
  shares (AFL, IBM, MCD and others), which makes a false drop at 2025-06-30.
- **Fix:** flag or reject a one-quarter jump that reverses, and store share counts in
  Class B equivalents for BRK-B; or derive Piotroski's dilution signal from the `market_caps`
  share history once it is long enough.

## Polygon price backend: adjusted close and date index

- **Raised:** 2026-10-21, packet 38.
- **Problem:** `_fetch_polygon` in `data/market_data.py` requests `adjusted=true` and stores that
  adjusted close as both `close` and `adj_close`, so `close` is not the raw close yfinance
  stores. The `dates` line sits inside the `for bar` loop (rebuilt per bar; harmless but
  wrong). Polygon's range end is inclusive, unlike yfinance's, so the packet-38 end date can
  include a partial bar before 16:30 New York. Backend not in use (no `POLYGON_API_KEY`).
- **Fix:** before enabling Polygon, fetch unadjusted and adjusted bars (or compute adj_close),
  move `dates` out of the loop and pass an inclusive end date.

## Insider factor: 10b5-1 plan flag

- **Raised:** 2026-10-21, packet 39 data audit.
- **Problem:** Form 4s mark trades made under a Rule 10b5-1 plan (pre-scheduled, so weak
  signal), but the flag is not parsed or stored, and the insider factor weights planned and
  discretionary sales alike.
- **Fix:** parse and store the 10b5-1 flag on `insider_transactions`; down-weight planned
  trades in the insider factor and analyzer.

## Insider factor: CEO/CFO title matching

- **Raised:** 2026-10-21, packet 39 data audit.
- **Problem:** `_is_ceo_cfo` substring-matches `officerTitle`, so it misses "President and
  Principal Executive Officer" style titles and can match unrelated titles that contain "ceo"
  or "cfo"; it ignores the Form 4 `isOfficer`/`isDirector` relationship flags.
- **Fix:** match on whole words plus the principal executive/financial officer wording; check
  against a sample of stored titles.

## Insider cluster flags: window bookkeeping

- **Raised:** 2026-10-21, packet 39 data audit.
- **Problem:** `_flag_cluster_buys` upserts one flag per purchase date and never removes flags
  whose purchases left the lookback or were deleted (the packet 39 rebuild clears them once);
  overlapping windows count the same cluster several times; `flagged_at` is never set, so it
  is always NULL.
- **Fix:** recompute a ticker's flags from scratch each run (delete then insert), merge
  overlapping windows, and set `flagged_at`.

## Form 4/A amendments and transaction date sanity

- **Raised:** 2026-10-21, packet 39 (out of scope there).
- **Problem:** Form 4/A amendments are not fetched, so corrected filings never replace the
  original; transactions carry no filed date, and a mistyped transaction date (years before or
  after the filing) is stored as is and can enter or leave the scoring window wrongly.
- **Fix:** fetch 4/A and replace the amended accession's rows; store the filed date and flag
  transaction dates far from it.

## Per-CIK fetch for share-class siblings

- **Raised:** 2026-10-21, packet 39.
- **Problem:** `sec_filings.accession_no` is globally unique, so one share class (GOOG or
  GOOGL) owns each filing row. The other class never sees those accessions as already fetched
  and re-downloads the same Form 4s (and 10-K/10-Q/8-K text) every run; the inserts are no-ops.
- **Fix:** fetch submissions once per CIK and record seen accessions per CIK, writing insider
  rows for every ticker of the issuer.

## Layer 5 does not see the what-if book

- **Raised:** 2026-10-23, packet 46 (out of scope there).
- **Problem:** nightly runs use `--whatif`, so Layer 4 commits nothing and `portfolio_positions`
  stays empty. Layer 5 then runs the factor risk model, correlation monitor and factor monitor
  on no positions (the 2026-10-22 log shows correlation 0.000 and 0 effective bets), so the
  what-if risk check says nothing about the 20/20 book Layer 4 just proposed.
- **Fix:** pass the Layer 4 target book to Layer 5 in what-if mode (for example a target-book
  file or table), or state in the run output that what-if risk checks are skipped.

## Dead factor-exposure flagging

- **Raised:** 2026-10-23, packet 46.
- **Problem:** `portfolio/factor_exposure.py` `flag_unusual_exposures` is never called, and its
  `_historical_spreads` pools up to 5,000 rows across dates into one value per factor, so it
  could never reach its 10-value minimum.
- **Fix:** delete both, or rebuild them on the packet 46 portfolio-history baseline if a Layer 4
  exposure flag is wanted.
