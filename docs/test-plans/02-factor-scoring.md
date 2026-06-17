# Test Plan: factor-scoring

Path: `docs/test-plans/02-factor-scoring.md`

## What changed
- Initial implementation of Layer 2: Factor Scoring Engine. All code shipped in the founding commit.

## Acceptance criteria coverage
- AC1: `tests/test_momentum.py` — `TestRet`, `TestComputeStructure`, `TestInsufficientHistory`, `TestRelativeStrength`, `TestHighProximity`, `TestMomentumScore`; `tests/test_value.py`; `tests/test_quality.py`; `tests/test_growth.py`; `tests/test_revisions.py`; `tests/test_short_interest.py` — `TestLongConvention`, `TestMissingData`, `TestStructure`; `tests/test_insider.py` — `TestNetFlow`, `TestCeoWeight`, `TestClusterFlag`, `TestStructure`; `tests/test_factor_institutional.py`
- AC2: `tests/test_composite.py` — `TestMissingFactor`; `tests/test_loader.py` — `TestLoadUniverse`, `TestLoadPrices`, `TestLoadFundamentals`, `TestLoadInsider`, `TestLoadVix`
- AC3: `tests/test_composite.py` — `TestValidateWeights`, `TestStructure`, `TestDirectionLabels`, `TestCompositeOrdering`
- AC4: `tests/test_regime_weights.py` — `TestResolveRegime`, `TestAdjustWeights`, `TestNormalise`
- AC5: `tests/test_revisions.py` — `TestDegenerateMode`, `TestRevisionDeltas`, `TestStructure`
- AC6: `tests/test_short_interest.py` — `TestLongConvention`; `tests/test_composite.py`
- AC7: `tests/test_insider.py` — `TestCeoWeight`, `TestNetFlow`, `TestClusterFlag`
- AC8: `tests/test_crowding.py` — `TestInsufficientHistory`, `TestCrowdingDetection`, `TestComputeFactorReturns`
- AC9: `tests/test_scoring_db.py`
- AC10: `tests/test_scoring_db.py`

## Edge cases
- From spec:
  - Sector with fewer than `min_sector_size` (5) tickers for a sub-factor: fall back to universe-wide ranking, log a warning
  - Tickers with NaN raw values receive sector median score (50.0) after ranking — not zero or NaN
  - Revisions degenerate mode: fewer than 30 days of `analyst_estimates` history → all three sub-factors default to 50.0; log a warning if > 50% of universe is degenerate
  - VIX data unavailable: NORMAL regime weights used with a logged warning (no crash)
  - Crowding detection skipped when fewer than 60 days of `factor_scores` history exist (first-run scenario)
  - Insider factor: no open-market transactions in 90-day window → both sub-factors default to 50.0
  - SHORT composite: `composite.py` applies `100 − short_interest_score` inline rather than storing a separate column

- Additional adversarial cases:
  - All tickers in a sector have identical raw values for a sub-factor: percentile ranking should assign 50 to all (ties), not produce NaN or divide-by-zero
  - Factor weights in config do not sum to 1.0 (e.g. sum = 0.99 due to rounding): startup validation must raise an error before any scoring occurs
  - A ticker appears in `sp500_universe` but has no rows in `daily_prices`: it must be excluded from momentum scoring entirely (not given a spurious 50) and a warning must be logged
  - VIX close exactly on the boundary (15.0 and 25.0): boundary conditions for LOW_VOL/NORMAL/HIGH_VOL regime must be unambiguous — VIX = 15.0 should produce NORMAL, VIX = 25.0 should produce NORMAL, VIX = 14.9 LOW_VOL, VIX = 25.1 HIGH_VOL
  - Crowding flag triggered exactly at deviation = 0.40: the threshold is exclusive (> 0.40), so 0.40 must not be flagged; 0.401 must be flagged
  - Re-running scoring for the same date: upsert into `factor_scores` must be idempotent — no duplicate rows and no change to previously written scores

## Notes
- Flaky risks: External API calls are mocked in tests; no network dependency.
- Determinism considerations: All tests use deterministic fixtures.
