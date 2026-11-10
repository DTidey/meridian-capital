Spec: `docs/specs/64-churn-hysteresis.md`

Spec only: docs-only PR, no code. Implementation follows in a later PR on this packet, which
will check AC1-AC10 here and add the replay numbers.

## Summary

On 2026-11-09, 12 positions opened by the 11-06 orders were closed in full (MOS, VICI, APA,
ATO, YUM, COHR, WELL, AJG, CRL, TTWO, MSI, DE), and 13 entry trades were trimmed by the
turnover budget. Two causes, checked read-only against the DB:

- Hard VIX regime switch (HIGH_VOL on 11-05 and 11-06, NORMAL on 11-09) reshuffles the
  sector-rank composite. With NORMAL weights every day, 11-06 -> 11-09 Spearman is 0.971,
  against 0.905 stored. For DE, TTWO, AJG, MSI and YUM the 11-06 composite under NORMAL
  weights equals the 11-09 value.
- Layer 4 takes a strict top 20 per side each night. Closures are never trimmed but are
  charged to the budget first, so a rotation is counted twice and the replacements get trimmed.

A rank-only replay (10-27 to 11-09, idealised 20/20 book) with an exit rank of 35 cuts name
exits from 92 to 41 and round trips within 2 nights from 61 to 18. Mean held side rank moves
from 10.5 to 12.0.

Files: new `docs/specs/64-churn-hysteresis.md` and `docs/test-plans/64-churn-hysteresis.md`
(planned); the "Back-and-forth trading across consecutive nights" item is removed from
`docs/backlog.md` because this spec replaces it. Merged with main after packet 63 (#131), and
the spec now treats held-name prices as available.

## Key recommendation

- Incumbent buffer: enter at the top 20 per side; keep a held name while it is a candidate on
  the same side with side rank <= 35 (`portfolio.incumbent_exit_rank`). Book size stays fixed.
  NEUTRAL or flipped names are closed.
- Regime weights: linear blend across VIX bands (high_vol 20-30, low_vol 12-18) on the mean
  of the last 5 VIX closes. Enter/exit thresholds were rejected: they would still have jumped
  fully on 11-05 and 11-09, and they need stored state.
- Turnover budget: rotation credit. Closures stay never-trimmed and charged; opens and adds
  in the same book are exempt up to that book's closure value.
- No minimum holding period; it overlaps with the buffer.
- First commit is a read-only replay tool; set the exit rank and smoothing days from it.

## Open questions for David

1. The VIX close on 2026-11-04 is 16.61, between 20.83 and 28.18. Is it a bad print?
2. Is the rotation credit in scope for this packet, or a follow-up? It changes packet 57.
3. Should the buffer also cover band exits (e.g. keep a long while composite >= 70)?
4. Is the 12-18 low_vol band right, given VIX sat at 15-16 through October?

## Validation

- `make lint` passes; `make test` passes (no code changed).
- Security: docs only, no security impact in this PR; the spec's security section covers the
  implementation (read-only replay, no new external calls).
