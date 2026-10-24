# Data audit sign-off (2026-10-21)

Pre-paper-trading audit of the data and scoring pipeline, kept as a checklist so each finding can be
signed off as it is fixed.

## How to use this checklist

- **Tick an item only when its "Verify" check passes against production data** after the fix is
  deployed and any backfill has run. A merged PR alone is not enough.
- When ticking, replace `Signed off: -` with the date and the evidence, e.g.
  `Signed off: 2026-10-23, APH close 2026-08-28 = 78.87`.
- Update `Fix:` when a packet is planned or merged.
- An item fixed differently from the plan, or accepted as is, gets a one-line reason.
- **Gate for full paper trading:**
  - every Critical item is signed off;
  - every High item is signed off or explicitly accepted with a reason.
- Code locations are as of the audit (`main` at `7b64beb`) and may have moved since.

## Scope and method

- **Sample tickers:** AAPL (Sep FY), NVDA (Jan FY, 10:1 split), JPM (bank), BRK-B (share
  class), GOOGL (dual class), XOM, PLD (REIT), NEE (utility), CMG (50:1 split), NKE (May FY)
  and SMCI (late filer). Universe-wide checks were run alongside.
- **References:**
  - SEC XBRL companyfacts and frames;
  - EDGAR submissions, Form 4 XML and 13F information tables;
  - a fresh yfinance pull.
- **Method:** five read-only audits, one per data area. Every Critical finding was re-checked
  against the database or the code.
- **Verdict:** not ready for paper trading. The scoring code is faithful: re-running it on the
  stored data reproduced every factor, composite and combined score exactly. The problems are
  in the ingested data, in parts of the factor logic, and in the bookkeeping from portfolio
  construction to orders.

## Status

| Severity | Items | Signed off |
|---|---|---|
| Critical | 12 | 8 |
| High | 11 | 6 |
| Medium / low | 23 | 0 |

## Critical: wrong trades or broken bookkeeping

- [x] **C1. Prices: splits and dividends after the initial load never reached older rows.**
  - Evidence:
    - APH's 2:1 split shows as a 50% one-day loss (stored close 157.74 to 79.28 on 2026-08-31).
    - APH (composite 2.7), CVNA (6.4), KLAC (5.3) and CRWD (14.7) are SHORT on these artefacts.
    - Dividend drift moves momentum by a median 2.7 points, and by more than 10 points for 33
      tickers.
  - Root cause: `data/market_data.py:185-186` fetches only from the last stored date.
  - Fix: packet 38, PR #91 (merged, deployed and backfilled 2026-10-21).
  - Verify:
    - after `run_data.py --refresh-prices`, APH's stored close for 2026-08-28 is about 78.87
      (split-adjusted);
    - no stored one-day return exceeds 40% without a corporate action;
    - adj_close matches a fresh yfinance pull within 0.1% for the sample tickers.
  - Signed off: 2026-10-22. APH close 2026-08-28 = 78.87; the 13 remaining one-day moves over
    40% are real events (e.g. MRNA 2026-08-19 on 199M shares, CNC, GL, FISV, VIX spikes); none
    since. close and adj_close match a fresh yfinance pull exactly (max gap 0.0000%, 266 days
    from 2025-10-01) for the 11 sample tickers plus APH, CVNA, KLAC and CRWD. CRWD SHORT to
    NEUTRAL (momentum 14.7 to 94.7); APH momentum 1.3 to 46.7.

- [x] **C2. Fundamentals: bad share counts.**
  - Evidence:
    - BRK-B is stored as 1.43M shares (the Class A equivalent), giving book/price of 981x and a
      composite of 98.7, LONG.
    - CME's 2026-06-30 count is 359,275 (a 1000x glitch), making it LONG at 90.8.
    - MNST and APH are exactly double the SEC cover-page count. Corrected 2026-10-22: not a
      fault. MNST split 2:1 on 2026-08-11 and APH on 2026-09-03; the stored counts are
      split-adjusted like our prices, and the cover pages compared predate the splits.
  - Root cause: `data/fundamentals.py:116-127` has no share-class handling and no sanity check.
  - Fix: packet 42, PR #99 (merged and deployed 2026-10-22). Fixed differently from the plan:
    market cap comes from Yahoo's `marketCap` (new `market_caps` table, every share class) with
    a one-day glitch guard, not from corrected balance-sheet counts. SEC has no single
    cover-page count for multi-class filers.
  - Verify:
    - after the first nightly `market_caps` snapshot and a rescore, the market cap used for
      BRK-B, CME, MNST and APH is within 5% of Yahoo `marketCap`;
    - BRK-B's book/price and composite are no longer extreme.
  - Signed off: 2026-10-24, from the 2026-10-23 nightly run. Stored market caps for 2026-10-23
    against a fresh Yahoo pull: BRK-B $1,160.7B, MNST $90.5B, APH $182.7B (all 0.00%), CME
    $109.9B (+0.46%). All 503 universe tickers have a 2026-10-23 snapshot; the 16 balance-sheet
    fallbacks are non-universe tickers. CME LONG 94.7 to NEUTRAL 67.1 (book/price 98.7 to
    26.3). BRK-B book/price 100 to 68.4 (981x before); composite 82.9, LONG, accepted as a
    genuine signal now that its market cap is correct.

- [x] **C3. Insider: a company's own trades in other issuers are booked as insider trades.**
  - Evidence:
    - BRK-B's insider_score of 100 comes entirely from Berkshire buying Lennar; the corrected
      score is 15.8.
    - About 35 tickers are affected, including UBER, ARES, HOOD, GS, BAC and BX.
  - Root cause: `data/sec_data.py:185-244` never checks the Form 4 `issuerCik`.
  - Fix: packet 39, PR #92 (merged, deployed and rebuilt 2026-10-21).
  - Verify:
    - after `run_data.py --rebuild-insider`, no `insider_transactions` row comes from a Form 4
      whose issuer is neither the ticker's CIK nor a configured predecessor;
    - BRK-B has 6 open-market rows (63 before).
  - Signed off: 2026-10-21. Rebuild inserted 62,449 rows for 478 tickers; 831 filings had no
    universe owner. BRK-B 6 open-market rows; insider_score 100 to 17.1 on the 2026-10-20
    rescore; UBER 1.2 to 98.8.

- [x] **C4. Insider: the unique key silently dropped real transactions.**
  - Evidence:
    - An exercise and a same-day sale of the same share count collapsed into one row (TT kept the
      M and lost the S).
    - 2,390 open-market rows ($5.4B of sales) are missing.
  - Root cause: key `(ticker, accession_no, insider_name, date, shares)` at `data/db.py:125`.
  - Fix: packet 39, PR #92 (merged, deployed and rebuilt 2026-10-21).
  - Verify:
    - TT accession `0001628280-26-054280` has both the M and the S row;
    - the constraint is `(ticker, accession_no, txn_index)`.
  - Signed off: 2026-10-21. TT accession has M and S rows of 43,778 shares; constraint
    `insider_transactions_ticker_accession_no_txn_index_key`; no row has a null txn_index.
    TT insider_score 95.2 to 1.2 on the 2026-10-20 rescore.

- [x] **C5. Estimates: one-day forward EPS glitches feed straight into scores.**
  - Evidence:
    - PGR forwardEps was 1012 instead of about 16.3 on 24, 25 and 28 September.
    - That spike passes through rev_30d, rev_60d and rev_90d until late December.
    - CRM showed -1.90 for two days.
  - Root cause: `data/estimates.py:18` stores values unvalidated; revisions use one prior
    snapshot.
  - Fix: packet 41, PR #96 (merged and deployed 2026-10-21).
  - Verify:
    - a forward EPS move above 50% that does not persist is not used;
    - PGR's revision scores in the window affected by the 24-28 September glitch are not
      extreme.
  - Signed off: 2026-10-22. Revisions now come from Yahoo's EPS trend (`estimate_trends`), not
    stored `forwardEps` snapshots, so the September PGR spike is no longer an input; PGR
    revisions_score 51.3 on 2026-10-21. The glitch guard fired once on 2026-10-21 (FRT, Yahoo
    returned 0.0 for the 7-day-ago figure) and fell back to the 7-day-ago consensus.

- [x] **C6. Estimates: a fiscal-year roll-over is scored as an analyst upgrade.**
  - Evidence:
    - forwardEps is the next-fiscal-year estimate, so it steps up when a company reports its
      fourth quarter.
    - MU and COST had all three revision windows at 100 on 2026-10-20.
    - About 400 calendar-year names roll over in January and February.
  - Root cause: no fiscal period is stored; `factors/revisions.py:161` compares across years.
  - Fix: packet 41, PR #96 (merged and deployed 2026-10-21).
  - Verify:
    - revisions compare estimates for the same fiscal period;
    - a roll-over date produces no revision spike for MU, COST or AAPL.
  - Signed off: 2026-10-22. EPS trend compares current vs 7/30/60/90-day-ago figures for the
    same period ('0y', '+1y'). On 2026-10-21 MU 97/95/92 (genuine: +1y 178.2 to 206.8 in 30
    days), COST 91/74/65 (was 100/100/100 on 2026-10-20), AAPL 11/28/8.

- [x] **C7. Estimates: the 90-day revision is neutral for every ticker every Friday.**
  - Evidence: rev_90d = 50 for 503 of 503 tickers on 9 and 16 October, against 8-78 on other
    days.
  - Root cause: `factors/loader.py:26` sets the lookback equal to the 90-day window, and 90 days
    before a Friday is always a Saturday.
  - Fix: packet 40, PR #95 (merged and deployed 2026-10-21).
  - Verify: the count of `rev_90d = 50` on a Friday is in line with other weekdays.
  - Signed off: 2026-10-24. `rev_90d = 50` for 8 of 503 tickers on Friday 2026-10-23, the
    same as 2026-10-21 and 10-22 (503 of 503 on Friday 2026-10-16).

- [x] **C8. Earnings calendar: no data since 14 October, and failures are silent.**
  - Evidence:
    - Every nightly run logged "0 upcoming events stored".
    - 59 tickers have no next date.
    - 122 tickers carry superseded dates, which creates false earnings blackouts.
  - Root cause: `data/earnings_calendar.py:66-68` swallows exceptions; upserts never remove old
    dates.
  - Fix: packet 40, PR #95 (merged and deployed 2026-10-21).
  - Verify:
    - `max(fetched_at)` is less than one day old after the nightly run;
    - a run storing no events fails loudly;
    - no ticker has more than one future date.
  - Signed off: 2026-10-24, from the 2026-10-23 nightly run. `max(fetched_at)` 2026-10-23
    22:02; 503/503 fetched, 415 upcoming events stored, 1 superseded removed; no ticker has
    more than one future date (55 on 2026-10-22). Loud failure: coverage below 95% logs an
    ERROR (`data/yahoo_info.py` `log_low_coverage`), covered by
    `tests/test_earnings_calendar.py`; not yet exercised in production. 89 universe tickers
    have no future date, mostly names that have just reported; no check covers this.

- [ ] **C9. Combined score: AI is blended only into AI-covered names, and the scales are mixed.**
  - Evidence:
    - 27 of 108 LONGs and 41 of 97 SHORTs were quant-NEUTRAL names with no AI review.
    - PLD went from 23 to 3 (SHORT) and NEE from 23 to 3 (SHORT) with no AI.
  - Root cause: `analysis/combined_score.py:106-107`.
  - Fix: packet 43 (PR pending). Direction is the Layer 2 direction, so only AI-reviewed
    candidates can be LONG or SHORT; within each side, quant and AI ranks are blended 60/40
    and mapped onto the side's band. The sector re-rank is gone.
  - Verify: no ticker becomes LONG or SHORT only because it lacks AI coverage; recheck PLD and
    NEE.
  - Signed off: -

- [ ] **C10. Positions: position tracking cannot follow a real book.**
  - Evidence:
    - The target book is written as positions before any fill.
    - Reconcile then deletes those rows, so fills are never recorded.
    - Reconcile stores shorts unsigned, so the next rebalance reads them as longs.
    - Rows the reconcile creates have no sector or weight, so pre-trade checks reject them.
  - Root cause: `portfolio/rebalance.py:95-97, 240-276`; `execution/broker.py:126-146`;
    `execution/executor.py:403-419`.
  - Fix: packet 44 (PR pending). Alpaca is the book of record: `execution/book_sync.py`
    rebuilds `portfolio_positions` from broker positions (signed shares, full metadata) and
    writes a whole-book `portfolio_history` snapshot, before Layer 4 every night and after
    execution. Rebalance and the executor no longer write positions. The end-to-end Verify
    needs packet 49 (order handling) first.
  - Verify: an end-to-end rebalance, execute and reconcile on the paper account leaves
    `portfolio_positions` equal to Alpaca's positions, with signed shares.
  - Signed off: -

- [ ] **C11. NAV: never marked to market, so the circuit breakers cannot fire.**
  - Evidence: unrealised P&L is always 0, realised P&L is never booked, and NAV stays at
    100,000.
  - Root cause: `reporting/nav_series.py:49-50`; `portfolio/state.py:47`.
  - Fix: packet 50 (planned; split from 44 on 2026-10-24).
  - Verify: `portfolio_nav` updates daily from Alpaca account equity, and drawdown moves on a
    losing day.
  - Signed off: -

- [ ] **C12. Execution: orders are marked cancelled but left live at Alpaca.**
  - Evidence:
    - Orders go in at 18:00 ET as DAY limit orders.
    - After 120 seconds they are marked CANCELLED in the database only, so they fill the next
      morning unrecorded.
    - Their limits are set from a close two sessions old.
  - Root cause: `execution/executor.py:101-107, 153-155, 367-369`.
  - Fix: packet 49 (planned; split from 44 on 2026-10-24): limit-on-open (OPG) orders after the
    close, order status synced from Alpaca the next night.
  - Verify:
    - no order is CANCELLED in the database while open at Alpaca;
    - limits are set from a live quote.
  - Signed off: -

## High: wrong inputs, weaker risk control

- [x] **H1. Prices: scores used the previous day's prices and VIX.**
  - Evidence: yfinance treats `end` as exclusive, and the code passed `end=today`.
  - Root cause: `data/market_data.py:162,195`.
  - Fix: packet 38, PR #91 (merged, deployed and backfilled 2026-10-21).
  - Verify:
    - `factor_scores` for date D are written only when SPY has a bar dated D;
    - `regime_state.vix_close` equals the same-day VIX close.
  - Signed off: 2026-10-22. The nightly run fetched with end 2026-10-22 (exclusive); SPY has a
    2026-10-21 bar (773.09) and scoring ran for 2026-10-21. `regime_state.vix_close` 16.68
    for 2026-10-21 equals the stored ^VIX close that day (17.31 for 2026-10-20 likewise).

- [x] **H2. Prices: 4,194 empty rows stored and never repaired.**
  - Evidence: 460 tickers are empty on 2026-09-22 alone.
  - Root cause: `data/market_data.py:83-88`.
  - Fix: packet 38, PR #91 (merged, deployed and backfilled 2026-10-21).
  - Verify: `daily_prices` has zero rows with no open, high, low and close.
  - Signed off: 2026-10-21. Zero empty rows for universe and benchmark tickers. 20 remain
    for 10 tickers no longer in the universe (AVB, BLDR, CTRA, CTVA, EA, EQR, PSKY, SATS,
    TAP, WBD), which are neither fetched nor scored. Accepted.

- [ ] **H3. Beta and covariance: one missing ticker drops that day for every ticker.**
  - Evidence:
    - A universe-wide beta used 12 of 60 days.
    - JPM's beta is 0.55 alone and -0.80 in the universe call.
    - `_adjust_beta` has no effect, and betas are not shrunk (range -0.6 to 4.4).
  - Root cause: `portfolio/beta.py:37-43`, `portfolio/mvo_optimizer.py:186`,
    `portfolio/optimizer.py:309-316`.
  - Fix: not yet planned.
  - Verify: a ticker's beta is the same whether computed alone or with the universe.
  - Signed off: -

- [ ] **H4. Portfolio: the target book breaches the pre-trade net limit.**
  - Evidence:
    - Targets are 0.90 long and 0.60 short (net +0.30) against a 0.15 limit.
    - The configured net and gross bounds are never read.
  - Root cause: `config.yaml:162-166` vs `204-206`; `portfolio/optimizer.py:230-276`.
  - Fix: not yet planned.
  - Verify: the simulated target book passes every pre-trade check.
  - Signed off: -

- [ ] **H5. Execution: BRK-B is never converted to Alpaca's BRK.B.**
  - Evidence: orders and shortability checks fail; reconcile would treat the two spellings as
    separate tickers.
  - Root cause: `execution/executor.py:102`, `execution/short_check.py:34`,
    `execution/broker.py:68-75`.
  - Fix: packet 44 (PR pending): `execution/symbols.py` maps BRK-B to BRK.B for orders and the
    shortability check, and back for broker positions.
  - Verify: a BRK-B paper order is accepted and reconciles to `BRK-B`.
  - Signed off: -

- [x] **H6. Value factor: single-quarter ratios, missing-value bug, wrong EBIT.**
  - Evidence:
    - Using one quarter instead of trailing twelve months moves 134 tickers more than 20
      percentile points on FCF yield.
    - `nan or 0.0` sets shareholder yield to neutral for 143 tickers, including CMG's $654M
      buyback.
    - GOOGL's EBIT exceeds its revenue.
  - Root cause: `factors/value.py:39, 63-69`; `data/fundamentals.py:62`.
  - Fix: packet 42, PR #99 (merged and deployed 2026-10-22).
  - Verify:
    - value inputs use four contiguous quarters;
    - shareholder yield is scored when only one component is missing;
    - EV/EBIT uses operating income.
  - Signed off: 2026-10-22, after `run_data.py --recompute-ratios` (4,822 of 6,355 rows) and
    the 2026-10-21 rescore. Tickers with a neutral shareholder yield 150 to 10; GOOGL EV/EBIT
    rank 81 to 20.

- [x] **H7. Fundamentals: every stored ROE is the true value minus one.**
  - Evidence: AAPL is stored as -0.72, and the sign flips for negative-equity companies. The
    dashboard shows these values.
  - Root cause: `data/fundamentals.py:174`.
  - Fix: packet 42, PR #99 (merged and deployed 2026-10-22).
  - Verify: stored ROE equals net income divided by equity (AAPL 2026-06-30 about 0.28).
  - Signed off: 2026-10-22. AAPL 2026-06-30 roe 0.2771; no stored row differs from net income
    divided by equity.

- [x] **H8. SEC: 29 tickers have no SEC data, and two map to the wrong company.**
  - Evidence:
    - GEV, BLK, KVUE, VLTO, XYZ and others have no SEC data.
    - BNY maps to a BlackRock municipal fund.
    - XOM is pinned to its old CIK.
  - Root cause: `data/sec_data.py:54-71, 342-352`.
  - Fix: packet 39, PR #92 (merged, deployed and rebuilt 2026-10-21).
  - Verify:
    - after the next full SEC run, every universe ticker has stored filings;
    - BNY's filings come from CIK 1390777.
  - Signed off: 2026-10-24, from the 2026-10-23 full SEC run (1,822 filings across 227
    tickers). Every universe ticker has stored filings. BNY: 19 filings from CIK 1390777; one
    stray Form 4 (accession 0001193125-26-047047, 2026-02-11, issuer CIK 1137390, no insider
    rows) was left from the old mapping and deleted on 2026-10-24, which also clears the
    nightly "SEC CIK for BNY changed" warning. XOM: 4 filings from its new CIK 2115436 and
    52 from predecessor 34088; its CIK-changed warning is expected.

- [ ] **H9. 13F: REITs and renamed names never resolve; GOOGL counts depositary shares.**
  - Evidence:
    - 14 tickers (SPG, DLR, XOM, OKE and others) have no institutional data.
    - Citadel's GOOGL is stored as 8.19M shares, against 2.99M actual Class A shares.
  - Root cause: `data/institutional.py:262-313, 366`.
  - Fix: not yet planned.
  - Verify:
    - those 14 tickers have 2026-06-30 holdings;
    - Citadel's GOOGL is 2,990,581 shares.
  - Signed off: -

- [x] **H10. Estimates: 60-73 tickers drop out of each night's snapshot.**
  - Evidence: mostly tickers late in the alphabet; XOM's latest estimate was from 14 October.
  - Root cause: `data/estimates.py:28-30` has no retry, and failures are logged at debug.
  - Fix: packet 40, PR #95 (merged and deployed 2026-10-21).
  - Verify: `analyst_estimates` covers at least 98% of the universe on each weekday.
  - Signed off: 2026-10-24. Nightly runs stored 503/503 tickers on 2026-10-22 and 2026-10-23
    (433-443 before packet 40).

- [ ] **H11. Transcripts: non-earnings documents are fed to the earnings analyzer.**
  - Evidence:
    - For about 21 tickers, the latest "transcript" is an M&A release, an investor deck or an
      equity raise.
    - Six heavy filers (NEE, AXP, COF and others) are skipped permanently.
  - Root cause: `data/transcripts.py:24-45, 320-359`.
  - Fix: not yet planned.
  - Verify:
    - each ticker's latest transcript row is an earnings release (8-K Item 2.02);
    - NEE's Q3 release is present.
  - Signed off: -

## Medium and low

Each needs a packet, a backlog entry, or an explicit acceptance.

- [ ] **M1. Short interest is stamped with the fetch date, not the settlement date.**
  - si_change alternates between one and two settlement periods.
  - Float changes leak into it, because it is measured as a share of float.
  - `data/short_interest.py:15-27`. Fix: -
- [ ] **M2. Revisions are measured in absolute dollars, not percent.** High-EPS names dominate.
  `factors/revisions.py:161`. Fix: packet 41, PR #96 (merged and deployed 2026-10-21)
- [ ] **M3. Missing values are filled with 50 before ranking.**
  - Insider has no data for 173 tickers.
  - `ins_net_flow` ranks the smallest net seller above names with no activity.
  - `factors/_utils.py:33-35`, `factors/insider.py:74`. Fix: -
- [ ] **M4. Dead and duplicated sub-factors.**
  - `inst_simultaneous_open` is 50 for every ticker.
  - `mom_rel_strength` ranks identically to `mom_6m`.
  - Ranking the binary cluster flag penalises sectors with many flags.
  - Fix: -
- [ ] **M5. The risk analyzer rates 89% of filings HIGH.** Its score is effectively a constant
  of about 7 within the AI composite. Fix: -
- [ ] **M6. The insider analyzer rates half of tickers as moderate sellers.** Routine selling is
  penalised. Fix: -
- [ ] **M7. Regime flips 16 times in 115 days with no hysteresis.**
  - A missing VIX defaults to NORMAL.
  - Tail-risk returns 0.0 when VIX is missing.
  - `factors/regime_weights.py:24-30`, `risk/tail_risk.py:166-178`. Fix: -
- [ ] **M8. The pipeline ran on market holidays.**
  - Packet 38's freshness gate stops scoring on those days.
  - Confirm on the next holiday (Thanksgiving, 2026-11-26).
  - Fix: packet 38.
- [ ] **M9. Filings and Form 4s refresh only on Fridays.**
  - Packet 39 adds `run_all.py --forms`.
  - The Mon-Thu cron change to `--forms 4` is the owner's call.
  - Fix: packet 39 plus cron.
- [ ] **M10. Fundamentals lag SEC filings by one to three weeks or more.**
  - 32 tickers have a blank Q2 row.
  - NKE, COST, MU and others are missing their latest quarter.
  - Fix: -
- [ ] **M11. No filed date on fundamentals or insider rows.**
  - This is latent look-ahead for backtests; live scoring is not affected.
  - The insider half is also in `docs/backlog.md` (Form 4/A amendments and transaction date
    sanity).
  - Fix: -
- [ ] **M12. Fundamentals: negative equity gives a negative D/E.** The most leveraged names rank
  as least leveraged (LOW, MAR, DPZ, YUM). `factors/quality.py:100-105`. Fix: packet 42
  (PR #99, merged 2026-10-22): negative equity with debt ranks as most leveraged.
- [ ] **M13. Sector-agnostic factors distort banks, insurers and REITs.**
  - Gross margin, D/E, Altman Z and CFO/NI do not suit banks.
  - REIT FCF is null.
  - Fix: -
- [ ] **M14. Value mixes periods.** `groupby('ticker').last()` takes the last non-null value per
  column, so one row can mix periods (DPZ). `factors/value.py:39`. Fix: packet 42 (PR #99, merged
  2026-10-22): TTM flows and one balance-sheet quarter.
- [ ] **M15. Transcripts are wrongly labelled.**
  - Quarter labels are wrong for non-December fiscal years.
  - All rows are press releases, because FMP returns 403.
  - `data/transcripts.py:165-183`. Fix: -
- [ ] **M16. 47% of 10-Ks are cut off at 500,000 characters.** `data/sec_data.py:170`. Fix: -
- [ ] **M17. Insider factor uses raw dollars and ignores 10b5-1 plans.** Also in
  `docs/backlog.md` (10b5-1 plan flag). Fix: -
- [ ] **M18. Cluster flags include non-discretionary buys and have bookkeeping bugs.** Also in
  `docs/backlog.md` (cluster flags: window bookkeeping). Fix: -
- [ ] **M19. CEO/CFO weighting uses substring matching.** Also in `docs/backlog.md` (CEO/CFO
  title matching). Fix: -
- [ ] **M20. crowding_flags lack baselines for 26 of 28 factor pairs.**
  - They flag whenever |corr| > 0.40.
  - The optimiser does not read them.
  - `factors/crowding.py:171-178`. Fix: -
- [ ] **M21. AI coverage fell to about 20% on 2026-09-16 to 09-21 with no alert.**
  `analysis/combined_score.py:104-107`. Fix: -
- [ ] **L1. `ai_composite` is written as the float NaN, not NULL.** SQL counts and averages are
  then wrong. `analysis/combined_score.py:155`. Fix: -
- [ ] **L2. Stale state from 2026-05-21.**
  - Eight PENDING approvals are still open.
  - Eight `portfolio_history` rows date from then.
  - Clear them before go-live (needs owner approval).
  - Fix: -

## Confirmed sound at audit time

- **Raw prices:**
  - Close prices matched yfinance exactly wherever no split or dividend followed the load.
  - OHLC values were sane and there were no duplicate rows.
  - The 2024 NVDA and CMG splits were correct.
- **Fundamentals vs SEC (919 values):**
  - The median gap was 0.000%.
  - Net income, assets, equity, CFO, capex and dividends had no value off by more than 2%.
  - Fourth quarters derived as the full year minus nine months matched exactly.
- **Filings:** EDGAR filing lists matched exactly for 9 of the 11 sample tickers.
- **Form 4 parsing:** 18 filings checked field by field, all correct; derivative rows were
  excluded.
- **13F:** Berkshire and Citadel holdings matched exactly apart from GOOGL and XOM. The
  filing-date gate prevents look-ahead.
- **Score recomputation:**
  - Composite and combined scores reproduced exactly.
  - AI sub-scores traced to current-model results, with none expired.
- **Direction counts:** the constant 108 LONG / 97 SHORT split comes from fixed sector sizes,
  not stale data.
- **Insider rows dated 2027:** two filer typos on a non-scoring transaction code, with no
  effect on scores.
