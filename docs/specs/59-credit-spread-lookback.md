# Credit Spread z-score: Full 252-Observation Lookback and Threshold Review

**Spec file:** `docs/specs/59-credit-spread-lookback.md`
**Spec slug:** credit-spread-lookback
**Status:** Draft (phase 1: awaiting owner decision D1 before implementation)
**Owner:** DTidey
**Date:** 2026-11-05

## Problem statement
Tail-risk CAUTION (`REDUCE_GROSS_20`) fired on every run from 2026-10-26 to 2026-11-04, always
on the credit-spread z-score (1.32-2.70 against `risk.tail_risk.credit_spread_sigma: 1.0`),
never on VIX (16.6-21.2, below `vix_caution: 25`). Source: investigation
`docs/investigations/2026-11-05-underinvested-book.md` (PR #126, finding 2).

- `risk/tail_risk.py` `_get_credit_spread_z_fred` requests FRED `BAMLH0A0HYM2` from
  `score_date - (credit_lookback_days + 30)` **calendar** days, then keeps
  `.tail(credit_lookback_days)` **observations**. 282 calendar days hold about 195-205
  business-day observations, so the z-score uses ~205, not the 252 that spec 05 ("vs 252-day
  mean/std") and the config name promise.
- The cached window on 11-04 (`cache/fred_hy_spread.parquet`): 205 observations,
  2026-01-26 to 2026-11-03, mean 2.886%, std 0.213%, latest 3.40%, z = 2.42. Checked
  read-only on 2026-11-05: the same latest value scores z = 2.54 over the last 120
  observations and 1.68 over the last 60. In a window this calm, a 1-sigma threshold fires
  on a widening of about 21bp above the window mean.
- The HYG price fallback (`_get_credit_spread_z_hyg`, used when FRED fails) already takes the
  last 252 rows, so the two paths disagree on the window today.

The lookback is a bug (the code does not do what the config and spec say). The threshold is
a policy choice; fixing the lookback alone may not stop CAUTION, because the extra ~47
observations (roughly November 2025 to January 2026) could not be checked in phase 1 (no
network access from this session; the cache holds only 205 points).

Packet 58 changes what CAUTION does (book gross 150% -> 120% instead of churn on traded
names), which makes a false CAUTION much cheaper. This packet makes the signal correct.

## Design choices (owner decision needed)

### D1. Threshold policy (after the lookback fix)
- Option A: keep `credit_spread_sigma: 1.0`; fix only the lookback. No policy change; CAUTION
  probably keeps firing in a tight-spread year (z above 1 roughly one day in six even for
  a normal distribution, and spreads trend, so it fires for weeks at a time).
- **Option B (recommended): fix the lookback and raise `credit_spread_sigma` to 2.0.** One
  config value; fires only on a large widening relative to the past year. Today's 2.42 (205
  obs) would still fire, so phase 2 reports the 252-obs z for the last 30 nights at 1.0, 1.5
  and 2.0 before the value is merged, and the owner confirms the number.
- Option C: fix the lookback and require both `z >= sigma` and an absolute level
  (`credit_spread_min_pct`, for example 4.0% OAS). Avoids firing when spreads are near
  historic lows however they move; needs a second config key and a level the owner must pick.
- Option D: a longer window (for example 3 years) so the mean and std span a full cycle.
  Depends on how much history FRED serves for ICE BofA series (to be checked in phase 2);
  changes `credit_lookback_days` and the HYG fallback alike.

### D2. Fetch window (no real choice; stated for review)
Fetch `ceil(credit_lookback_days x 7 / 5) + 30` calendar days (about 383 for 252), keep the last
`credit_lookback_days` observations on or before `score_date`, and log a WARNING with the count
when fewer than `credit_lookback_days` remain (still computing z from what is there, as today,
down to the existing 10-observation minimum).

## Scope
In scope:
- `risk/tail_risk.py` `_get_credit_spread_z_fred`: fetch window per D2; WARNING on a short
  window; INFO line with observation count, mean, std, latest and z.
- `config.yaml` `risk.tail_risk.credit_spread_sigma`: value per D1 (and a new key only if D1-C).
- `docs/specs/05-risk-management.md`: AC5 and the credit-spread section state the observation
  count and threshold.

Out of scope / non-goals:
- What CAUTION does to the book (packet 58).
- VIX thresholds (unchanged).
- The HYG fallback window (already 252 rows).
- Persisting the FRED series in the database.

## Assumptions
- FRED `observation_start` filters by observation date and returns all later observations, as
  the current code already relies on.
- Business days per calendar day is about 5/7 minus holidays; the +30 buffer covers US holidays
  (about 10 a year) with margin.

## Proposed behavior / API
### Public interface
- No signature change. `_get_credit_spread_z_fred` computes its start date from the
  observation count it needs.

### Examples
```python
# score_date 2026-11-04, credit_lookback_days 252
# before: start 2026-01-26 (282 days) -> 205 obs
# after:  start ~2025-10-17 (383 days) -> 252 obs kept (of ~262 fetched)
```

## Acceptance criteria
- AC1: With a FRED response covering more than enough business days, the z-score is computed
  from exactly `credit_lookback_days` observations on or before `score_date`.
- AC2: The request's `observation_start` is at least `ceil(credit_lookback_days x 7 / 5) + 30`
  calendar days before `score_date`.
- AC3: When fewer than `credit_lookback_days` numeric observations are available (but at least
  10), the z-score is still computed and a WARNING names the count.
- AC4: Non-numeric FRED values (`"."`) are skipped and do not count towards the lookback
  (unchanged behaviour, now tested against the new window).
- AC5: CAUTION by credit fires only when `credit_spread_z >= credit_spread_sigma` with the
  owner's chosen value (D1); a z just below it is NORMAL (when VIX is also below caution).
- AC6: An INFO log line reports observation count, mean, std, latest spread and z; the API key
  never appears in any log line.
- AC7: Spec 05 AC5 and the credit-spread section state the observation-based window and the
  threshold.

## Security considerations
- Auth/authz impact: none.
- Input handling or injection risk: none; FRED values parsed as floats as today.
- Secrets or credential handling: `FRED_API_KEY` is sent only as a request parameter; AC6 adds
  a test that it is absent from log output. No change to where it is stored (`.env`).
- Data exposure or privacy impact: none.
- File system access impact: same cache file, ~50 more rows.
- Network or external service impact: one FRED request per call, slightly longer range.
- Dependency or supply-chain impact: none.
- Security notes for reviewers/testers: check that the new INFO line logs values, not the
  request URL.

## Edge cases
- FRED returns fewer rows than requested (series gap, new year): AC3 path.
- `score_date` on a weekend/holiday: last observation on or before it (unchanged).
- Latest observation missing (`"."`) for `score_date`: previous valid value is latest
  (unchanged).
- std near 0: returns 0.0 (unchanged).

## Rollout
1. Land first (smallest, a bug fix), before packets 58 and 57.
2. Phase 2 posts the 252-obs z for the last 30 nights at each candidate threshold in the PR
   draft, then the owner confirms D1's value.
3. Revert: config value or code revert; no schema change.

## Verify in paper trading
- Layer 5 log: the new INFO line shows 252 observations (or a WARNING with the count).
- `cache/fred_hy_spread.parquet` holds 252 rows after the run.
- Tail-risk state per night matches the threshold table in the PR draft.

## Test guidance
- AC1 -> `tests/test_risk_tail_risk.py::TestCreditLookback::test_uses_exact_lookback_obs`
- AC2 -> `TestCreditLookback::test_observation_start_covers_business_days`
- AC3 -> `TestCreditLookback::test_short_window_warns_and_computes`
- AC4 -> `TestCreditLookback::test_missing_values_not_counted`
- AC5 -> `tests/test_risk_tail_risk.py::TestTailRiskCaution::test_credit_threshold_boundary`
- AC6 -> `TestCreditLookback::test_info_line_without_api_key`
- AC7 -> review of the spec 05 diff (documentation AC)

## Decision log
- 2026-11-05: Spec written from the 2026-11-05 investigation. The prior session history
  (searched 2026-11-05) shows no owner decision on `credit_spread_sigma`, the lookback or
  FRED; CAUTION values were only reported (284a3bba 2026-10-30T07:34Z, f1142635
  2026-11-04T11:56Z). Raising the threshold is therefore a new policy decision, left to the
  owner (D1).
