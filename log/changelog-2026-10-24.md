# Changelog 2026-10-24

## Packet 47: filelock advisories

- Upgraded across a major version (3.29.4 -> 4.2.4) rather than ignoring: `pip-compile -P
  filelock` resolved with no other pin moving, so no parent caps it below 4.
- Checked the consumers directly, since the test suite never imports filelock: pip-audit ran
  clean and `make precommit` rebuilt every hook environment through virtualenv from an empty
  `PRE_COMMIT_HOME`.
- Numbered 47: the data audit plan still reserves 43 and 44.

## Packet 43: combined score

- AI reorders, it does not select: the owner chose this over an AI veto (demote a candidate
  when the blend crosses 50) and over keeping the sector re-rank. Any re-rank across all 503
  names keeps a fixed share at each extreme, so a candidate AI marks down is always replaced by
  an unreviewed name.
- Ranks within the side, not quant composite + AI percentile: inside the LONG candidates the
  quant composite spans only 80-100 while an AI percentile spans 0-100, so the nominal 60/40
  blend would let AI drive about three quarters of the order (2026-10-23: 7 of the top 20
  longs matched quant's top 20, against 10 with within-side ranks).
- Mid-rank `(rank - 0.5) / n`: ranks average exactly 50, the neutral rank given to a candidate
  with no AI result, and a lone candidate ranks 50.
- Found while scoping: the short book's conviction tilt rewards the weakest shorts. Backlog
  item; own packet.

## Packet 48: short book conviction tilt

- An `ascending` flag on `_apply_conviction_tilt`, matching the selection sort in `optimise`,
  rather than ranking by distance from 50: the same order on packet 43's bands, but the meaning
  stays tied to the book rather than to where the score bands sit.
- Reproduced end to end before the fix: with non-binding caps, the weakest selected short
  (score 40) got 4.3% against 2.9% for the strongest (score 2).

## Packet 44: book of record

- Owner's decisions: Alpaca is the book of record; the sync runs in `--whatif` runs too
  (reading the broker is bookkeeping); 44/49/50 split, with order handling (C12) in 49 as
  limit-on-open orders and NAV (C11) in 50.
- Replace, not reconcile: the old reconcile patched discrepancies and had three writers
  (rebalance, executor, reconcile) that disagreed on sign. One writer rebuilt from the broker
  cannot drift.
- Signed shares, because Layer 4 (`generate_trades`, optimiser) and the risk model already
  assumed them; only the tear sheet's sector net needed changing. Signed shares also fix
  FORCE_CLOSE's `delta_shares` for shorts.
- Sign taken from the position side, and market value / P&L computed from signed shares, so an
  API that reports short qty signed or unsigned gives the same row.
- A failed sync stops a real run before Layer 4 (no rebalance against a stale book) but not a
  `--whatif` run.

## Packet 49: order handling

- OPG (limit-on-open) was the agreed plan, but Alpaca rejects OPG orders submitted between
  9:28am and 7:00pm New York time; the nightly run reaches Layer 6 at about 17:30-18:30 New
  York. The owner chose queued DAY limits over adding a 00:30 UK execution cron.
- Record, do not wait: after the close nothing can fill within the old 120-second poll, so
  polling only produced false CANCELLED rows. The next night's `sync_orders` runs before the
  book sync, so fills and positions are recorded together.
- Existing status names kept (dashboard, `--status`); an order that partly fills and expires is
  CANCELLED with its `filled_shares`, so the slippage stats (FILLED only) exclude it.
- No close for the score date means no order, rather than trading on an older price.
- If open orders cannot be listed, nothing is submitted: a duplicate order is worse than a
  missed day.
- C12's Verify said "limits from a live quote"; no live quote exists after the close, so it is
  amended to the close of the session that just ended.

## Packet 50: NAV from the broker

- Alpaca equity is NAV: it already includes realised P&L, costs and cash, which no sum of
  unrealised P&L can. Written in the book-sync transaction so positions and NAV share a date.
- `build_nav_series` kept as the step that fills SPY and drawdown, but no longer computes NAV;
  otherwise it would overwrite the recorded equity every night.
- Circuit breakers measure returns on the previous recorded NAV, not the config NAV, so a fund
  smaller or larger than configured triggers at the intended percentages. The peak comes from
  the recorded series, not `risk_state.json`, so it cannot carry a stale config-based peak.
- No NAV for the score date (sync failed) skips the P&L checks with a warning rather than
  replaying yesterday's move.
- Sizing moves to the recorded NAV as well; config `nav_usd` is only the fallback. Both are
  $100,000 today, so nothing changes on merge.
- Turnover from fills: since packet 44 a snapshot is written every night, and the old
  market-value-diff method counted price drift as trading.

## Packet 51: book targets

- Owner chose near-neutral 80/70 (net +0.10, the portfolio section's net maximum) over fully
  neutral 75/75 or widening the limits for the +30% tilt.
- Targets stay explicit and are validated rather than derived from `gross_exposure` and
  `net_exposure_max`: they remain readable in the config, and the bounds that nothing read are
  now enforced, so the three statements cannot drift apart again.
- MVO falls back to the conviction optimiser on any error; the fallback validates the targets
  too, so a bad config still stops Layer 4.
- Dry run on 2026-10-23: gross 150%, net 10%, net beta -0.33 (H3: the beta adjustment has no
  effect), so H3 lands before the first real run.

## Packet 52: betas

- On 2026-10-23 the -0.33 net beta was real, not the day-dropping bug: all 40 names had full
  history, and the shorts average beta 1.35 against 0.77 for the longs. Shrinkage alone gave
  -0.19, so a working adjustment was needed regardless.
- Owner chose reweighting within books (no SPY hedge, limits unchanged) and Blume shrinkage.
- The old `_adjust_beta` multiplied the short book by one factor and renormalised it to the
  short target, an exact no-op.
- The tilt needed its own bounded scaling: the existing clamp-then-renormalise step makes every
  weight equal once a strong tilt pushes them all to a bound, so net beta was not monotone in
  the tilt and bisection failed. Bounded scaling fixes names at a bound (one side per pass) and
  shares the rest of the gross among the others.
- Returns are computed on the shared calendar, so a gap leaves the day empty instead of a
  multi-day return being compared with SPY's one-day return.
- Dry run on 2026-10-23: gross 150%, net 10%, net beta -0.150; read-only pre-trade simulation of
  the whole target book: 24 pass, 16 resized for earnings blackout, none rejected (H4 Verify).
- Review: a book with fewer names than gross / max_pos (e.g. 10 shorts at a 5% cap for 0.70)
  was silently shrunk by the tilt's bounded scaling, and the one-sided check could accept an
  overshoot; now such books skip the tilt with a warning and |net beta| is checked both ways.
- The tilt's reach under the 5% cap is limited (about 0.08-0.10 of net beta with little
  within-book spread); beyond that the warning and the pre-trade veto take over. Noted rather
  than widened: wider caps are a sizing decision.

## Docs: Alpaca paper trading guide

- Rewritten around the nightly flow packets 44, 49 and 50 created (orders submitted after the
  close, recorded the next night; Alpaca as book of record and NAV), rather than patched:
  the old "execute during market hours" step now fails with `no_close`.
- The supervised path documented is `run_all.py --dry-run` then `run_execution.py --execute`
  the same evening: `--whatif` commits no approvals, so it cannot be followed by `--execute`.
- Code mismatches found (ADV chunking never applied, `execution.alpaca_paper` unused, spec 06
  AC8 versus dry-run behaviour) go to the backlog; this change is docs only.
