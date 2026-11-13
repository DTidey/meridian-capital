Spec: `docs/specs/66-pretrade-sector-gross.md`

## Acceptance Criteria

- [x] AC1: With one sector over the limit, an opening trade in another sector passes check 5.
- [x] AC2: A trade that reduces its own size in an over-limit sector is approved.
- [x] AC3: A trade that adds to its own size in an over-limit sector is rejected, naming its own sector; limit stays 0.25.
- [x] AC4: `generate_trades` caps every priced target at floor(cap x NAV / price) shares, cap = stricter of the two `max_position_pct`.
- [x] AC5: A Layer 4 target above 5% is approved by `run_pre_trade` at the cap, not rejected.
- [x] AC6: The 2026-11-12 situation: other-sector trades and the Industrials reduction approved, the Industrials add rejected.
- [x] AC7: Targets at or below the cap and unpriced rows are unchanged.

## Summary

2026-11-12: Layer 5 rejected 38 of 42 trades; only 4 exits went out.

- 35 rejections (`Industrials pro-forma gross 26.9% > 25%`, including MOS, COP, BEN): check 5
  summed gross for every sector of the pro-forma book and rejected on the first one over the
  limit. As the pro-forma book is the same for every trade (packet 44), one breaching sector
  blocked every opening trade. `risk/pre_trade.py` now checks the trade's own sector only, and
  only a trade that grows its own absolute weight can fail it.
- 3 rejections (CF, DVA, AIZ, `POSITION_SIZE`): the conviction-tilt optimiser renormalises
  after clipping at 5% (top long about 5.05%) and `generate_trades` rounds to the nearest
  share. `portfolio/rebalance.py` now caps each rounded target at floor(5% x NAV / price)
  shares. The pre-trade check stays as the backstop.
- No limit values changed.

## Spec

- Spec: `docs/specs/66-pretrade-sector-gross.md`
- Test plan: `docs/test-plans/66-pretrade-sector-gross.md`
- PR draft path: `.ai/pr-description/66-pretrade-sector-gross.md`

## Security Review

- [x] Security considerations reviewed in `docs/specs/66-pretrade-sector-gross.md`
- No auth, secrets, network or query change. More trades reach execution, but every one still
  passes the same limits, and Layer 4 orders can only get smaller.

## Validation

- 11 of the first 17 new tests failed before the fix (the regression test reproduced the 11-12
  reason on MOS and `POSITION_SIZE: 5.1%` on CF); all 18 pass after.
- `make lint`: pass. `make test`: 1795 passed.
- `make security`: Bandit clean; pip-audit fails on `soupsieve==2.9` (CVE-2026-126192,
  CVE-2026-126190), a transitive pin this packet does not touch; it needs its own dependency
  bump.

## GitHub Checks

- Required checks for `main`: `CI / test`, `CodeQL / analyze`

## Changelog

- [x] `CHANGELOG.md` under `## Unreleased`.

## Open Risks

- The optimisers cap a sector per side (25% long, 25% short, 5% net), pre-trade caps it gross
  at 25%. Layer 4 will keep planning adds in a sector like Industrials and Layer 5 will keep
  rejecting them; nothing trims the sector back under 25%. Backlogged ("Optimiser sector cap is
  per side; pre-trade sector cap is gross").
- On nights the cap binds the book sits slightly below target gross (the capped excess is not
  redistributed).
- All adds in a breaching sector are rejected together, even if accepting some would keep it
  under 25% (whole-book pro-forma, packet 44).

Generated with [Claude Code](https://claude.com/claude-code)
