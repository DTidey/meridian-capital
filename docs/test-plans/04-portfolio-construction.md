# Test Plan: portfolio-construction

Path: `docs/test-plans/04-portfolio-construction.md`

## What changed
- Initial implementation of Layer 4: Portfolio Construction. All code shipped in the founding commit.

## Acceptance criteria coverage
- AC1: `tests/test_optimizer.py` — `TestOptimizerWeights`, `TestOptimizerEdgeCases`
- AC2: `tests/test_optimizer.py` — `TestOptimizerSelection`
- AC3: `tests/test_optimizer.py` — `TestEarningsHaircut`
- AC4: `tests/test_optimizer.py` — `TestOptimizerEdgeCases`
- AC5: `tests/test_optimizer.py` — `TestOptimizerWeights`, `TestOptimizerEdgeCases`
- AC6: `tests/test_mvo_optimizer.py` — `TestMvoOptimiser`
- AC7: `tests/test_rebalance.py` — `TestActionClassification`, `TestTurnoverBudget`, `TestPriorityColumn`
- AC8: `tests/test_state.py` — `TestSavePositions`, `TestLoadPositions`; `tests/test_rebalance.py`
- AC9: `tests/test_rebalance_schedule.py` — `TestEarningsWarnings`, `TestFomcWarnings`, `TestOptionsExpiryWarnings`, `TestThirdFriday`
- AC10: `tests/test_state.py` — `TestGetNav`, `TestLoadPositions`

## Edge cases
- From spec:
  - MVO non-convergence (`result.success == False`) or any exception: fall back to conviction-tilt and log a warning — no crash
  - Earnings blackout: position weight halved; surplus redistributed equally across remaining positions in the same book; re-normalise after redistribution
  - Liquidity cap: position trimmed to `adv_max_pct × ADV × price / NAV`; surplus redistributed; re-normalise
  - Turnover budget: full closures (delta = 100% of current) are never trimmed regardless of `turnover_budget_pct`
  - `--whatif` flag: proposed trades are printed but nothing is written to `position_approvals`, `portfolio_positions`, or `portfolio_history`
  - FOMC advisory: hardcoded 2026 dates — warns if score_date is within 5 calendar days of a meeting (not business days)
  - Third-Friday options expiry: warns if within 3 calendar days

- Additional adversarial cases:
  - All candidates have the same `combined_score`: conviction-tilt top-5% and top-10% multipliers apply to a non-empty subset — with all scores equal, 5% of 20 is 1 position; that one position receives the 1.5x multiplier; the rest use equal-weight base; re-normalisation must still sum to `target_long_gross`
  - ADV is zero for a ticker (new listing, no traded volume): `adv_max_pct × 0 = 0` would cap the position to zero weight; the ticker must be excluded from the portfolio rather than assigned a zero weight that causes a divide-by-zero in share-count calculation
  - Sector neutrality constraint and beta constraint are simultaneously violated: conviction-tilt applies sector neutrality first, then beta adjustment (fixed order per spec §5.6 steps 7 and 8); the final portfolio must satisfy both constraints after both post-processing steps
  - MVO covariance matrix is singular (e.g. two tickers with perfectly correlated returns): SLSQP will fail to converge; the fallback to conviction-tilt must trigger and be logged as a warning
  - `--rebalance` run with no candidates in `combined_scores` for today: the optimiser receives an empty DataFrame; it must raise a clear error rather than silently writing an empty portfolio
  - Portfolio history is append-only: calling `save_positions` twice on the same date must produce two distinct `portfolio_history` rows, not overwrite the first

## Notes
- Flaky risks: External API calls are mocked in tests; no network dependency.
- Determinism considerations: All tests use deterministic fixtures.
