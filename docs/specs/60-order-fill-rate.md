# Order Fill Rate: Limit Band for Queued DAY Orders

**Spec file:** `docs/specs/60-order-fill-rate.md`
**Spec slug:** order-fill-rate
**Status:** Done
**Owner:** DTidey
**Date:** 2026-11-05

## Problem statement
Layer 6 submits DAY limit orders at about 22:10 UK, after the US close; Alpaca queues them for
the next session and cancels what is unfilled at that session's close. Limits are the score
date's close +/- `execution.limit_slippage_pct` (0.005) (`execution/executor.py`
`_limit_price`; the config comment says "mid", the code uses the stored close). Any overnight
move of more than 0.5% against the order leaves it unfilled.

Evidence (investigation `docs/investigations/2026-11-05-underinvested-book.md`, PR #126,
finding 3, plus a read-only query on 2026-11-05 of `execution_orders` and `daily_prices`):

- 10-27 to 11-03: 65 orders, 43 filled (66%), 19 expired (`CANCELLED`), 3 `FAILED`; $64.3k of
  $90.3k ordered value filled (~71%).
- Not all 22 misses are market misses: 3 `FAILED` were bugs since fixed (CLX duplicate cover,
  packet 54; ZTS/TXN zero-share orders, packet 56), and about 6 were orders the owner cancelled
  (three breaker force-closes on 10-28, three wrong-side orders on 10-29). Of the remaining 13
  market expirations, 8 needed a band wider than 0.5% (next-session low above a BUY limit or
  high below a SELL limit): KIM 1.20% and 1.34%, CSGP 0.78%, CF 0.70%, MU 1.40%, CLX 1.88%,
  ZTS 2.22%, COHR 6.15%. The other 5 (ALLE, CSGP, AKAM, ZTS, NKE) traded at or through the
  limit by the daily high/low and still did not fill (see "Finding" below).
- Of those 8 gap misses, a 1.0% band would have filled 2 (CF, CSGP), 1.5% would have filled 5
  (adds KIM twice and MU), 2.0% would have filled 6 (adds CLX); COHR and ZTS need more.
- Whole universe, last 12 months (537 tickers, 133,751 ticker-days): the next session never
  reaches close +/- band on this share of days:

  | Band | BUY misses | SELL misses |
  |---|---|---|
  | 0.5% | 7.7% | 5.8% |
  | 1.0% | 3.8% | 2.9% |
  | 1.5% | 2.2% | 1.7% |
  | 2.0% | 1.4% | 1.1% |
  | 3.0% | 0.7% | 0.6% |

  Our realised miss rate is much higher than the universe's, which fits adverse selection: the
  model buys names with momentum and the misses cluster on names that kept moving the way the
  signal said.

Cost of a miss: the trade is usually re-planned the next night (Layer 4 diffs against the
synced book), at a price that already includes the gap, one day late, and it spends that
night's turnover budget again. So a wider band rarely costs more than the miss does; it mostly
removes the one-day delay.

## Prior owner decisions (not reversed by the recommendation)
- 630ff079, 2026-10-24T16:05Z: the owner first chose "Opening auction (Recommended)" (OPG
  limit-on-open, limits from the latest close +/- the configured buffer).
- 630ff079, 2026-10-24T16:54Z: after learning that Alpaca rejects OPG orders submitted between
  9:28am and 7:00pm New York time, the owner chose "Queued DAY limits (Recommended)" over
  "OPG + second cron" (a separate execution job at 00:30 UK; "cleaner auction pricing, but two
  schedules to keep in step"). Recorded in packet 49's decision log.
- The 0.5% buffer itself was never chosen: it appears in that option text only as the existing
  configured value. Re-proposing unfilled orders was never discussed.

## Design choices (owner decision needed)

### D1. How to raise the fill rate
- **Option A (recommended): keep queued DAY limits, widen the band to 1.5%.** One config value
  (`limit_slippage_pct: 0.015`) plus spec text; no schedule or order-type change, so the
  owner's 10-24 choice stands. On the data above it removes about 70% of universe-wide misses
  and 5 of the 8 recent gap misses. Trade-off: on a gap day the order fills up to 1.5% worse
  than the close, which is about what re-planning the next night would pay anyway.
- Option B: OPG orders from a second, execution-only cron at 00:30 UK (19:30 or 20:30 New York,
  always after the 7pm cut-off, including the weeks when UK and US clocks change on different
  dates). Market-on-open fills at the auction price every time; limit-on-open with a band
  still misses large gaps. This was the owner's first preference but was explicitly turned down
  for the second schedule; choosing it reverses packet 49's decision and needs
  `run_all.py`/cron changes, a guard so the 22:00 run no longer executes, and a way to recover
  if the 00:30 job fails.
- Option C: volatility-scaled band (`k x ATR(14) / close`, floor 0.5%, cap 3%). Adapts to
  volatile names such as COIN and CVNA, at the cost of ATR plumbing in the executor and a new
  parameter to tune. Can follow A if the fill rate stays low on volatile names.
- Option D: extended-hours DAY limits (`extended_hours=True`) so orders can also fill in the
  after-hours, overnight and pre-market sessions. Not recommended: thin liquidity, and paper
  simulation of those sessions may not reflect live fills.

### D2. Re-proposing unfilled orders
- **Option A (recommended): no explicit roll-forward.** Layer 4 already re-plans every night
  from the synced book, so an unfilled intent returns while the target still wants it. With
  packet 57 the turnover budget no longer drops a returning build-up trade. A roll-forward on
  top would duplicate Layer 4 and could re-send an order the new scores no longer want.
- Option B: GTC orders, or re-submit last night's unfilled orders. Stale intent; risks two
  live orders for one name (the executor's open-order check would then skip the new plan).

## Scope
In scope:
- `config.yaml`: `execution.limit_slippage_pct` per D1 (0.015 recommended); comment corrected
  ("from the score date's close", not "mid").
- `execution/executor.py`: `_limit_price` default argument aligned with the config value (the
  function default is only used by tests and direct callers); range check on the configured
  band before any order is submitted (AC5).
- `docs/specs/06-order-execution.md` AC2 and `docs/specs/49-order-handling.md` (note under the
  decision log) updated to the new band.

Out of scope / non-goals:
- Order type, time-in-force and schedule (owner's 2026-10-24 decision, unless D1-B is chosen,
  in which case this spec is rewritten before implementation).
- Slippage measured against the limit rather than the decision price (investigation finding 6;
  backlog "Execution metrics count only FILLED orders").
- ADV chunking (backlog).
- A dashboard fill-rate panel (verification below uses a query).

## Finding: the 5 orders that traded through their limit (phase 2, read-only)
Checked 2026-11-05 with Alpaca GETs only (order records and 1-minute bars; no order was
placed, cancelled or changed).

| Order (score date) | Limit | Status | Minutes at/through limit | 1.5% band: marketable min after 09:35 |
|---|---|---|---|---|
| ALLE BUY 12 (10-28) | 181.56 | expired | 09:30 only | 24 |
| CSGP BUY 4 (10-30) | 30.32 | expired | 09:30-09:32 | 0 |
| AKAM SHORT 2 (10-30) | 92.26 | expired | 09:30-09:34 | 43 |
| ZTS COVER 6 (10-30) | 80.12 | expired | 09:30 only | 0 |
| NKE SHORT 7 (11-02) | 38.92 | expired | 09:30 only | 7 |

- Every order was live as intended: LIMIT, DAY, not extended-hours, correct side and
  position intent, released by Alpaca at about 4am New York time and expired at the close.
  `execution_orders` holds no duplicate rows. Nothing in our code stopped them filling.
- In every case the price reached the limit only in the opening minute(s) (regular hours;
  the opening print and the first swing), then moved away for the rest of the session. The
  daily high/low used in phase 1 includes those prints.
- Alpaca paper trading fills a limit order only when the quote is marketable ("a
  non-marketable buy limit order will not be filled until its limit price is equal to or
  greater than the best ask price", docs.alpaca.markets/docs/paper-trading); a print at the
  limit does not mean the NBBO crossed it. These were near-misses at the open, not a bug.
- Not fixable in our code within this packet. The wider band helps: at 1.5%, ALLE, AKAM and
  NKE had marketable minutes later in the session; CSGP and ZTS moved further away.

## Assumptions
- Alpaca accepts limits at 1.5% from the last close for queued DAY orders (no price-band
  rejection for limit orders on S&P 500 names).
- Price increments: limits round to $0.01 as today (all names trade above $1).

## Proposed behavior / API
### Examples
```python
# close 100.00, band 1.5%
_limit_price("BUY", 100.0, 0.015)    # 101.50
_limit_price("SHORT", 100.0, 0.015)  # 98.50
# KIM 11-03: close 24.99, next low 25.325 -> 0.5% limit 25.11 missed; 1.5% limit 25.36 fills
```

## Acceptance criteria
- AC1: With `execution.limit_slippage_pct: 0.015`, BUY and COVER limits are
  `round(close x 1.015, 2)` and SELL and SHORT limits are `round(close x 0.985, 2)`, where
  `close` is the stored close for `score_date`.
- AC2: The band is read from config on every run; a config value of 0.005 reproduces today's
  limits exactly (no other change to order construction: DAY, limit, position intent, whole
  shares).
- AC3: An approval with no stored close for `score_date` is still skipped (unchanged guard).
- AC4: `config.yaml` comment and spec 06 AC2 / packet 49 note state the band and that it is
  measured from the score date's close.
- AC5: `limit_slippage_pct` outside (0, 0.05] makes Layer 6 exit with an error naming the key,
  before any order is submitted.

## Security considerations
- Auth/authz impact: none.
- Input handling or injection risk: none.
- Secrets or credential handling: none.
- Data exposure or privacy impact: none.
- File system access impact: none.
- Network or external service impact: same number of Alpaca orders; limits up to 1.5% from
  the close instead of 0.5%, so a filled order can cost up to 1% more per order than today's
  worst case. Order sizes, sides and checks unchanged.
- Dependency or supply-chain impact: none.
- Security notes for reviewers/testers: confirm the band cannot be negative or above a sane
  maximum (a config typo such as 0.15 would allow 15% fills); AC5 rejects values outside
  (0, 0.05].

## Edge cases
- Config value outside (0, 0.05]: executor refuses to run with a clear error (AC5).
- Large gap beyond the band (COHR 6.15%): still unfilled; re-planned next night (D2-A).
- Partial fill then expiry: unchanged (`CANCELLED` with `filled_shares` > 0).

## Rollout
1. Land last (after 59, 58, 57), so the first nights with more and larger orders also use the
   wider band.
2. No schema change; revert by setting the config value back to 0.005.

## Verify in paper trading
- Layer 6 log: limits about 1.5% from the close (`Submitted BUY ... limit=`).
- Over 5 nights, read-only:
  `SELECT status, count(*), sum(ordered_shares * <close>) ... FROM execution_orders WHERE
  rebalance_date >= :d GROUP BY status`; target at least 85% of orders and 90% of value
  filled, excluding owner-cancelled orders. If below, revisit D1-C or D1-B.
- Compare the average fill price with the score-date close for filled orders (expect most
  within the band, a few at it).

## Test guidance
- AC1 -> `tests/test_execution_executor.py::TestLimitBand::test_buy_cover_above_close`,
  `test_sell_short_below_close`, `test_config_band_is_one_and_a_half_pct`
- AC2 -> `TestLimitBand::test_band_read_from_config`,
  existing `TestSubmission::test_limit_from_close_for_score_date`
- AC3 -> existing no-close skip test in `tests/test_execution_executor.py`
- AC4 -> review of the config and spec diffs (documentation AC)
- AC5 -> `TestLimitBand::test_band_out_of_range_refused`, `test_band_upper_bound_accepted`,
  `test_validation_before_alpaca_calls`

## Decision log
- 2026-11-05: Spec written from the 2026-11-05 investigation and a read-only replay of
  next-session highs/lows for every order since go-live. Recommendation keeps the owner's
  2026-10-24 order-type decision and changes only the never-chosen 0.5% value.
- 2026-11-05: Owner approved D1-A (queued DAY limits, 1.5% band, range check (0, 5%]) and
  D2-A (no roll-forward). No OPG orders or second cron. Implemented.
- 2026-11-05: The 5 touched-but-unfilled orders were checked read-only (table above): opening
  near-misses under Alpaca's quote-based paper fills, not a code fault; no fix.
- 2026-11-05: The range check runs at the start of `execute_approvals`, before approvals are
  read or Alpaca is called, so a bad value fails the run with nothing sent. A missing key
  falls back to 1.5%; an explicit non-number (including a quoted "0.015") is refused.
