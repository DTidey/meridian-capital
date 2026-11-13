Spec: `docs/specs/66-pretrade-sector-gross.md`

## Acceptance Criteria

- [x] AC1: With one sector over the limit, an opening trade in another sector passes check 5.
- [x] AC2: A trade that reduces its own size in an over-limit sector is approved.
- [x] AC3: A trade that adds to its own size in an over-limit sector is rejected, naming its own sector; limit stays 0.25.
- [x] AC4: `generate_trades` caps every priced target at floor(cap x NAV / price) shares, cap = stricter of the two `max_position_pct`.
- [x] AC5: A Layer 4 target above 5% is approved by `run_pre_trade` at the cap, not rejected.
- [x] AC6: The 2026-11-12 situation: other-sector trades and the Industrials reduction approved, the Industrials add rejected.
- [x] AC7: Targets at or below the caps and unpriced rows are unchanged.
- [x] AC8: Conviction-tilt plans every sector at or below the limit combined, keeps gross targets when there is room, positions within the cap.
- [x] AC9: MVO plans every sector at or below the limit combined; an infeasible solve falls back to conviction-tilt.
- [x] AC10: Too little sector room: conviction book short of gross, no sector over, WARNING logged.
- [x] AC11: `generate_trades` keeps each sector's whole-share target value within the limit after rounding.
- [x] AC12: Optimiser -> `generate_trades` -> `run_pre_trade` from Industrials at 26.9%: no sector rejection, both optimisers.
- [x] AC13: One value, `risk.pre_trade.max_sector_pct`, drives the plan and the check.

## Summary

2026-11-12: Layer 5 rejected 38 of 42 trades; only 4 exits went out.

- 35 rejections (`Industrials pro-forma gross 26.9% > 25%`, including MOS, COP, BEN): check 5
  summed gross for every sector of the pro-forma book and rejected on the first one over the
  limit. As the pro-forma book is the same for every trade (packet 44), one breaching sector
  blocked every opening trade. `risk/pre_trade.py` now checks the trade's own sector only, and
  only a trade that grows its own absolute weight can fail it.
- The 26.9% was Layer 4's plan: the optimisers capped each side of a sector at 25% (and net at
  5%), the check caps long + short combined at 25%. The 25% is combined (owner). Both
  optimisers now hold every sector's combined gross within `risk.pre_trade.max_sector_pct`, read
  through `portfolio/limits.py` by the optimisers, `generate_trades` and the check:
  - MVO: one linear constraint per sector; a solution still over raises into the existing
    conviction-tilt fallback.
  - Conviction-tilt: a breaching sector's two sides are scaled down together, each book is
    refilled from sectors with room (within the position cap); after the beta step a second
    pass only scales down. Not enough room: book left short of gross with a WARNING.
  - Per-side and net caps kept (per side no longer binds at 0.25 / 0.25).
- 3 rejections (CF, DVA, AIZ, `POSITION_SIZE`): clipping then renormalising in conviction-tilt
  (top long about 5.05%) and rounding to the nearest share. `generate_trades` now caps each
  rounded target at floor(5% x NAV / price) shares, and truncates a sector that rounding tips
  over 25%.
- No limit values changed. Owner decisions recorded as deliberate: no active trimming of an
  over-limit sector, all-or-nothing adds in an over-limit sector, small gross shortfall when a
  cap binds.
- Test fixtures that relied on per-side-only sector limits (whole book in one sector, or 1.50
  gross over 5 sectors) now set the combined limit explicitly; see the test plan.

## Spec

- Spec: `docs/specs/66-pretrade-sector-gross.md`
- Test plan: `docs/test-plans/66-pretrade-sector-gross.md`
- PR draft path: `.ai/pr-description/66-pretrade-sector-gross.md`

## Security Review

- [x] Security considerations reviewed in `docs/specs/66-pretrade-sector-gross.md`
- No auth, secrets, network or query change. More trades reach execution, but every one still
  passes the same limits, and Layer 4 targets only shrink or move to sectors with room.

## Validation

- New tests: 18 in `tests/test_pretrade_sector_gross_2026_11_12.py`, 13 in
  `tests/test_optimizer_sector_gross.py`; the regression and optimiser tests failed before the
  fix (MOS rejected with the 11-12 reason; Industrials planned at 27.3% by conviction-tilt).
- `make lint`: pass. `make test`: 1808 passed.
- `make security`: Bandit clean; pip-audit fails on `soupsieve==2.9` (CVE-2026-126192,
  CVE-2026-126190), a transitive pin this packet does not touch; known, out of scope.

## GitHub Checks

- Required checks for `main`: `CI / test`, `CodeQL / analyze`

## Changelog

- [x] `CHANGELOG.md` under `## Unreleased`.

## Open Risks

- First run after merge plans reductions in any sector above 25% combined (Industrials); with
  the turnover budget a trimmed reduction keeps that sector over in the pro-forma book, and its
  adds are rejected that night (deliberate: all-or-nothing adds, no active trimming).
- Refilling after a sector cap can move another sector's net beyond the 5% net cap (as the
  existing renormalisation already could); pre-trade does not check sector net.
- "Unknown" sector names are capped in Layer 4 but not checked by pre-trade (stricter in Layer 4).

Generated with [Claude Code](https://claude.com/claude-code)
