# Book Targets Within the Exposure Limits

**Spec file:** `docs/specs/51-book-targets.md`
**Spec slug:** book-targets
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-24

## Problem statement
Data audit item H4 (`docs/data-audit-2026-10-21.md`): the target book breaches the pre-trade
net limit. `config.yaml` states the book's intent three ways that disagree:

| Where | Setting | Net |
|---|---|---|
| `portfolio.target_long_gross` / `target_short_gross` (read by both optimisers) | 0.90 / 0.60 | +0.30 |
| `portfolio.gross_exposure`, `net_exposure_min` / `net_exposure_max` (read by nothing) | 1.50; 0.00-0.10 | |
| `risk.pre_trade.net_min` / `net_max`, `max_gross` (pre-trade veto) | -0.10-+0.15; 1.65 | |

Since packet 44, pre-trade checks each trade against the whole proposed book, so once a few
nights of rebalancing push the book past +15% net, every further opening long is rejected and
the book stalls lopsided.

Owner's decision (2026-10-24): near-neutral, long 0.80 / short 0.70 (gross 1.50, net +0.10),
matching the `portfolio` bounds; pre-trade keeps its limits as headroom for drift.

## Scope
In scope:
- `config.yaml`: `target_long_gross: 0.80`, `target_short_gross: 0.70`.
- New `portfolio/book_targets.py` `book_targets(config) -> (long_gross, short_gross)`: reads
  the targets and checks them against every configured bound; used by
  `portfolio/optimizer.py` and `portfolio/mvo_optimizer.py`.
- Tests, including one that pins the production config as consistent.
- Docs: spec 04 config block, audit H4 fix line, CHANGELOG, decision log.

Out of scope / non-goals:
- How the optimisers reach the targets (sector neutrality, beta adjustment: H3, next packet).
- Pre-trade limits themselves (unchanged).
- Drift of the held book away from targets between rebalances.

## Assumptions
- Net = long gross - short gross; gross = long + short.
- A bound is checked only when its key is present, so partial configs in tests keep working;
  the production config has every key.
- A floating-point tolerance of 1e-9 for the gross equality.

## Proposed behavior / API
### Public interface
- `portfolio.book_targets.book_targets(config: dict) -> tuple[float, float]`.

### Inputs / outputs
- Reads `portfolio.target_long_gross`, `target_short_gross` (defaults 0.80 / 0.70) and, when
  present, `portfolio.gross_exposure`, `net_exposure_min`, `net_exposure_max`,
  `risk.pre_trade.max_gross`, `net_min`, `net_max`.
- Raises `ValueError` naming the bound and the values when:
  - long + short differs from `gross_exposure`;
  - net is outside [`net_exposure_min`, `net_exposure_max`];
  - net is outside [`net_min`, `net_max`] or gross exceeds `max_gross` (the book could never pass
    pre-trade);
  - a target is negative.

## Acceptance criteria
- AC1: The production `config.yaml` targets are long 0.80 / short 0.70 and pass every bound
  (gross 1.50 = `gross_exposure`; net 0.10 within the portfolio and pre-trade bounds).
- AC2: `book_targets` raises `ValueError` for each inconsistent bound listed above, with a
  message naming the bound.
- AC3: Both optimisers take their long and short gross targets from `book_targets`, so an
  inconsistent config stops Layer 4 before any trade list is built.
- AC4: The conviction optimiser's target book on the production settings sums to 0.80 long and
  0.70 short before later constraints (sector, beta) adjust it.

## Security considerations
- No meaningful security impact: configuration values and in-memory arithmetic only. It lowers
  market exposure (net +0.30 to +0.10) and removes a configuration that the pre-trade veto would
  block.

## Edge cases
- Bounds absent (test configs): only the targets' signs are checked.
- Net exactly on a bound (e.g. 0.10): allowed.
- `gross_exposure` given but targets omitted: defaults 0.80 / 0.70 are checked like set values.

## Test guidance
- AC1 -> `tests/test_book_targets.py::test_production_config_consistent`
- AC2 -> `tests/test_book_targets.py::TestBounds::*`
- AC3 -> `tests/test_book_targets.py::test_optimisers_use_book_targets`
- AC4 -> `tests/test_optimizer.py::TestOptimizerWeights` (targets 0.80 / 0.70)

## Decision log
- 2026-10-24: Near-neutral 80/70 (owner's choice over fully neutral 75/75 and keeping the +30%
  net tilt with wider limits).
- 2026-10-24: Keep explicit targets and validate them, rather than derive them from
  `gross_exposure` and `net_exposure_max`: the targets stay visible in the config, and every
  stated bound is now enforced instead of ignored.
