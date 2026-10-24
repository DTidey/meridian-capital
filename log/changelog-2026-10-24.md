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
