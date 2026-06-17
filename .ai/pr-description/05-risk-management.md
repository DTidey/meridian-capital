## Summary
- Implements Layer 5, a post-optimisation pre-execution risk gate that stamps every PENDING trade in `position_approvals` as APPROVED or REJECTED via an 8-check pre-trade veto.
- Adds circuit breakers that monitor daily/weekly P&L and drawdown, applying SIZE_DOWN_30, CLOSE_ALL, or KILL_SWITCH (halt.lock) actions in priority order.
- Adds tail risk monitors (VIX + credit spread) and a factor risk model that decomposes portfolio variance via Barra-style OLS regression, writes a predicted covariance matrix, and computes MCTR per position.
- Persists all risk metrics, alerts, and circuit-breaker state to `cache/risk_state.json`; provides on-demand stress testing against historical and synthetic scenarios.

## Spec
- Spec: `docs/specs/05-risk-management.md`
- Test plan: `docs/test-plans/05-risk-management.md`
- PR draft path: `.ai/pr-description/05-risk-management.md`

## Acceptance Criteria
- [x] AC1: The system runs the full daily pipeline (factor risk model, pre-trade veto, circuit breakers, factor monitor, correlation monitor, risk state save) when `run_risk_check.py` is invoked with no flags.
- [x] AC2: The pre-trade veto evaluates all 8 checks in order for each PENDING trade and stamps each row APPROVED or REJECTED in `position_approvals`; closing/covering trades skip checks 2-8 and are always APPROVED.
- [x] AC3: The earnings blackout check (check 2) resizes the trade to 50% of target shares and marks it APPROVED with reason `BLACKOUT_REDUCED` rather than rejecting it.
- [x] AC4: The circuit breaker triggers SIZE_DOWN_30, CLOSE_ALL, or KILL_SWITCH based on daily/weekly P&L and drawdown thresholds, evaluated in priority order (drawdown first), and writes `cache/halt.lock` on KILL_SWITCH.
- [x] AC5: The tail risk monitor reduces APPROVED non-closure target shares by 50% when VIX >= 35, by 20% when VIX >= 25, and by 20% on credit spread z-score >= 1.0 sigma; VIX >= 35 dominates if both fire simultaneously.
- [x] AC6: The factor risk model decomposes portfolio variance into factor and specific components, computes MCTR per position, flags tickers where `|MCTR_pct| > 1.5 * |weight_pct|`, and writes `cache/predicted_cov_<date>.parquet`.
- [x] AC7: The correlation monitor computes per-book average pairwise correlation over 60 days, raises an alert when either exceeds 0.60, and computes effective number of bets via eigendecomposition of the combined correlation matrix.
- [x] AC8: The factor monitor raises a HIGH-priority alert when a long-minus-short factor z-score exceeds 1.5 in magnitude and that factor also has an active crowding flag from Layer 2.
- [x] AC9: `cache/risk_state.json` is read at startup and written at the end of every run, serving as the single source of truth for circuit-breaker memory and dashboard display; `is_halted()` checks `cache/halt.lock` file existence, not the JSON.
- [x] AC10: The `--whatif` flag runs all checks and computes all metrics but does not commit any changes to the database; `--clear-halt` deletes `cache/halt.lock` and exits.

## Security Review
- [x] Security considerations were reviewed and updated in the linked spec
- [x] No meaningful security impact beyond API key handling via environment variables

## Validation
- [x] `make lint`
- [x] `make test`
- [x] `make security`

## GitHub Checks
- Required checks for `main`:
  - `CI / test`
  - `CodeQL / analyze`

## Changelog
- [x] Add to `CHANGELOG.md` under `## Unreleased`

## Open Risks
- None. All tests passing. Code is read-only with respect to external systems during tests.
