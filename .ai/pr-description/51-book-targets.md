## Summary

Fixes data audit H4: the target book (0.90 long / 0.60 short, net +0.30) breached the
pre-trade net limit (+0.15), and the config's own net bounds (0-0.10) were never read. Since
packet 44, pre-trade checks each trade against the whole proposed book, so book-building would
stall once the book passed +15% net.

- Targets 0.80 long / 0.70 short (owner's choice): gross 1.50, net +0.10.
- New `portfolio/book_targets.py`: both optimisers take their targets from it, and it raises if
  the targets disagree with `gross_exposure`, `net_exposure_min/max`, or the pre-trade
  `max_gross` / `net_min` / `net_max`.
- Spec 04 config note; audit H4 fix line.

## Spec

- Spec: `docs/specs/51-book-targets.md`
- Test plan: `docs/test-plans/51-book-targets.md`
- PR draft path: `.ai/pr-description/51-book-targets.md`

## Acceptance Criteria

- [x] AC1: Production targets 0.80 / 0.70 pass every bound.
- [x] AC2: Each inconsistent bound raises `ValueError` naming it.
- [x] AC3: Both optimisers take their targets from `book_targets` (MVO's fallback included).
- [x] AC4: The conviction optimiser's book sums to 0.80 long / 0.70 short.

## Security Review

- [x] Security considerations reviewed in `docs/specs/51-book-targets.md` (no meaningful
  security impact; lowers market exposure)

## Validation

- `make lint`: pass
- `make test`: see PR checks
- `make security`: pass
- Production dry run (`run_portfolio.py --whatif --date 2026-10-23`): gross 150.0%, net 10.0%

## Risks and follow-ups

- The same dry run shows net beta -0.33 against the 0.15 optimiser limit and 0.20 pre-trade
  veto: H3 (betas and the beta adjustment), next packet, before the first real run.
- H4's Verify (the simulated target book passes every pre-trade check) waits for H3.
