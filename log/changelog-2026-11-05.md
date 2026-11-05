# Changelog 2026-11-05

## Packets 57-60: under-invested book (phase 1, specs only)

Source: `docs/investigations/2026-11-05-underinvested-book.md` (PR #126). Phase 1 writes specs
and test plans only; no code changes until the owner approves the design choices in each spec.
PR drafts (`.ai/pr-description/<nn>-<slug>.md`) are written in phase 2, when there is a
validation run to report.

- Four packets, not three: finding 2 mixes a bug (the FRED window holds ~205 observations, not
  252) with a design change (how REDUCE_GROSS and the earnings cut apply). Splitting the
  z-score fix into 59 keeps each PR small and lets the bug fix land first.
  - 57 turnover-build-up (finding 1)
  - 58 layer5-target-cuts (finding 2 design, findings 4 and 5)
  - 59 credit-spread-lookback (finding 2 data bug and threshold)
  - 60 order-fill-rate (finding 3)
- Recommended landing order 59 -> 58 -> 57 -> 60. 57 lets about 125% of NAV through on a
  build night; landing it before 58 would push that through the blackout x0.5 and
  reduce-gross x0.8 target cuts, which flip adds into reductions. 60 last, so the first nights
  of larger orders also get the wider band.
- 58 recommends tail-risk as a Layer 4 gross scale rather than scaling the delta in Layer 5:
  scaling only tonight's deltas never flips a trade, but the book still drifts back to 150%
  over a few nights, so the control would no longer limit gross at all.
- 58 supersedes packet 56's "a resize that turns an opening trade into a reduction is wanted".
  Session history shows that line was the assistant's framing, approved implicitly ("Please
  cancel the orders and proceed with packet 56"), and on 2026-11-04 the owner backlogged the
  opposite proposal ("never reverse its direction"). The spec still asks for explicit sign-off.
- 60 keeps queued DAY limits, the owner's 2026-10-24 choice (made after OPG was ruled out by
  Alpaca's 9:28am-7pm rejection window; OPG had been the first choice). The 0.5% band was
  never chosen; a read-only replay of next-session highs/lows shows 1.5% would have filled 5 of
  the 8 recent gap misses and cuts universe-wide misses from 7.7%/5.8% to 2.2%/1.7%
  (BUY/SELL).
- 59 could not compute the true 252-observation z in phase 1 (the cache holds 205 points and
  this session had no network access); phase 2 reports it before the threshold is merged.

## Packet 59 implemented (phase 2)

- FRED history fetched read-only through the app's FRED endpoint into /tmp (not the app cache,
  not the DB). The old window reproduced the logged z values (11-04 2.42, 10-29 1.32), which
  validates the replay.
- The lookback fix alone moves z by only -0.2 to +0.15 on the last 30 nights; the threshold
  does the work (CAUTION nights: 25 at 1.0, 18 at 1.5, 13 at 2.0).
- 2.0 kept as approved: the nights it still fires (10-02, 10-15..10-27, 11-02..11-04) follow a
  real ~70bp OAS widening since late September, which is what the control is for.
- The z is still computed from a short window (with a WARNING) rather than failing over to
  HYG: a 200-observation FRED z is closer to the intended signal than the HYG price proxy.
