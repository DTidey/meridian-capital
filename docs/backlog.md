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

## ADV chunking never applied

- **Raised:** 2026-10-24, paper trading doc review.
- **Problem:** `execution/executor.py` `_execute_one` calls `_chunk_orders(delta_shares, None,
  ...)`, so `execution.max_adv_pct` (2%) never splits an order. Pre-existing: before packet 49
  the ADV was also hard-coded to None ("ADV not stored on approvals"). Spec 06 AC3 and the
  architecture doc described chunking as working. Also, chunks would all be DAY orders for the
  same session, so splitting them would not spread the order across days anyway.
- **Fix:** decide whether to drop chunking (Layer 4 already caps positions at
  `portfolio.adv_max_pct` of ADV) or pass the 20-day ADV and spread chunks across sessions.

## Unused config and spec drift in Layer 6

- **Raised:** 2026-10-24, paper trading doc review.
- **Problem:** `execution.alpaca_paper` in `config.yaml` is read by no code; only the
  `ALPACA_PAPER` environment variable selects paper or live (`execution/broker.py`
  `get_client`). Spec 06 AC8 says `--dry-run` makes no Alpaca calls and writes no
  `execution_orders` rows; it connects to Alpaca, runs the order sync (which writes) and
  inserts `DRY_RUN` rows.
- **Fix:** remove `alpaca_paper` from `config.yaml` (or read it, with the env var overriding);
  restate spec 06 AC8 to match, or make dry runs read-only.

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

## pandas 4 and yfinance inplace

- **Raised:** 2026-10-29, packet 55.
- **Problem:** yfinance 1.7.0 calls pandas `sort_index`, `rename` and `set_index` with
  `inplace=True`, deprecated in pandas 3.1 (`Pandas4Warning`). Packet 55 hides these warnings
  in Layer 1. pandas 4 is expected to remove the keyword, which would break price downloads.
- **Fix:** do not merge a pandas 4.x bump until a yfinance release no longer uses `inplace`;
  then drop the filter in `data/market_data.py`.

## Circuit-breaker SIZE_DOWN scales only tonight's targets

- **Raised:** 2026-11-05, packet 58 (out of scope there).
- **Problem:** `risk/circuit_breakers.py` `_apply_size_down` (SIZE_DOWN_30, daily or weekly
  loss) multiplies the target of each APPROVED opening row by 0.7, like the tail-risk resize
  packet 58 removed. Untraded positions keep their size, so it does not cut book gross by 30%,
  can turn an add into a reduction, and Layer 4 plans the full target again the next night.
  Not yet triggered in paper trading.
- **Fix:** decide whether a loss response should scale the whole book in Layer 4 (as packet 58
  does for tail risk) or stay a one-night cut on opening trades.

## Pre-trade POSITION_SIZE rejects from whole-share rounding

- **Raised:** 2026-11-05, packet 58 (out of scope there; investigation finding 2).
- **Problem:** the optimiser clamps weights at exactly `portfolio.max_position_pct` (0.05),
  then `generate_trades` rounds to whole shares, which can land just above the pre-trade
  `max_position_pct` (0.05) and gets the trade rejected: MU 5.3%, ARE 5.0%, BA 5.1% and 5.0%
  between 2026-10-30 and 2026-11-03.
- **Fix:** round toward zero for positions at the cap, or give the pre-trade limit a small
  tolerance above the sizing cap.

## A held name with no stored close cannot be closed by the pipeline

- **Raised:** 2026-11-05, packet 61 (out of scope there).
- **Problem:** Layer 4 loads prices for SHORT/LONG candidates only, so a held name that left
  the scored universe gets price 0 (closure still planned, now correctly COVER/SELL). The
  executor then needs the stored close for the score date (`_closes_on`); a name dropped from
  price ingestion (e.g. removed from the S&P 500) is skipped as `no_close` every night and the
  position is never closed. Not seen yet: all 18 positions on 2026-11-05 are in the universe.
- **Fix:** keep ingesting prices for every held ticker, or fall back to the broker's last
  price for closing orders only.

## Direction flip in one order (BUY_TO_OPEN over an open short)

- **Raised:** 2026-11-05, packet 61 (out of scope there).
- **Problem:** a name that moves from short to long in one night is one BUY row for the whole
  delta (cover plus open), submitted with position intent `BUY_TO_OPEN` (SELL_TO_OPEN for long
  to short). Not verified whether Alpaca accepts an opening intent larger than the open
  opposite position, or fills only part of it.
- **Fix:** check Alpaca's behaviour on paper; if rejected, split into a closing order for the
  held quantity and an opening order for the rest.

## Candidate card status buttons act on every row

- **Raised:** 2026-11-06, packet 62 (question 4, packet 57 open risk).
- **Problem:** `dashboard/page_research.py` `_set_status` (Approve / Reject / Reset on each
  candidate card) updates every `position_approvals` row for (date, ticker) with no status
  filter. "Approve" turns a `TRIMMED` row `APPROVED`; "Reset" turns it `PENDING`, where the bulk
  execute button picks it up. The same buttons overwrite `REJECTED` (Layer 5) and already
  submitted rows. Spec 57 calls approving a `TRIMMED` row by hand deliberate, so packet 62 did
  not block it; packet 62's `already_submitted` skip stops a reset row from trading twice.
- **Fix:** decide which transitions the cards may make (e.g. only PENDING <-> APPROVED /
  REJECTED, and TRIMMED -> APPROVED with a confirmation), filter the update on the source
  status, and show TRIMMED distinctly on the card.

## Research page short-availability veto never fires

- **Raised:** 2026-11-06, packet 62.
- **Problem:** `_run_veto_checks` check 8 calls `execution.short_check.is_shortable(ticker)`
  with one argument; the function needs the client, cache dir and days, so it raises and the
  bare `except` swallows it. The executor's own `not_shortable` check still guards SHORT orders.
- **Fix:** pass the arguments or drop the check in favour of the executor's.

## Rotation credit in the turnover budget

- **Raised:** 2026-11-10, packet 64 (Decision 2: out of that packet, follow-up after the
  buffer and regime smoothing land).
- **Problem:** `portfolio/rebalance.py` `_apply_turnover_budget` charges full closures to the
  rotation budget first (`budget_remaining = budget - closures`) and never trims them, so on a
  high-churn night the closures use up the budget and their replacements are trimmed: each
  rotated dollar is charged twice. The packet 57 build-up allowance covers opens only up to
  the pre-trade gap, not the gross freed by that night's closures. Evidence, 2026-11-09: 12
  names opened by the 11-06 orders were closed in full and 13 opening or adding trades were
  TRIMMED (BUY 4, SHORT 7, SELL 1, COVER 1); the book stayed below target.
- **Fix:** spec 64 Design 3 option (d): closures stay never-trimmed, charged and ordered
  first; per book, opens and adds still charged after the build-up allowance are exempt up
  to that book's charged closure value (highest conviction first). Measure first: with the
  packet 64 buffer, closures per night fall (replay: 32 exits over 9 nights instead of 92),
  so check the trimmed count on live nights before building it. The replay
  (`portfolio/churn_replay.py`) has no trade sizing yet; the follow-up needs it to model
  trimming.

## Buffer for band exits

- **Raised:** 2026-11-10, packet 64 (Decision 3: deferred until the replay counts band exits).
- **Problem:** the packet 64 buffer keeps held names by side rank only; a held name that
  leaves the LONG/SHORT candidate band (turns NEUTRAL) is closed at once. Replay (read-only,
  `python -m portfolio.churn_replay`) at the chosen settings (blend 5 days, low_vol 13-17,
  exit rank 35): 58 of 358 exits over 2026-05-05 to 11-09 are band exits (about one in six);
  3 of 32 over 2026-10-27 to 11-09. Regime smoothing already cut the short window's band
  exits from 12 to 4 at exit rank 20.
- **Fix (if wanted):** a wider exit band on the composite (e.g. keep a long while
  composite >= 70, a short while <= 30); held-name prices exist since packet 63. Layer 3 gives
  NEUTRAL names no side-band `combined_score`, so the rule must use `composite_score`, and the
  replay should count how many band exits would stay inside the wider band before choosing.
