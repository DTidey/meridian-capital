## Summary
- Consumes the `combined_scores` table from Layer 3 and produces a target long/short portfolio with specific tickers, weights, and share counts subject to risk, liquidity, and concentration constraints
- Provides two optimisation methods: a conviction-tilt optimiser (default, always converges) and an optional Markowitz MVO optimiser via SLSQP that falls back to conviction-tilt on non-convergence
- Implements a rebalance generator that diffs current positions against the target, maps deltas to BUY/SELL/SHORT/COVER/HOLD actions, enforces a turnover budget, and estimates transaction costs
- Persists portfolio state to three new PostgreSQL tables (`portfolio_positions`, `portfolio_history`, `position_approvals`) and supports a `--whatif` preview mode that commits nothing

## Spec
- Spec: `docs/specs/04-portfolio-construction.md`
- Test plan: `docs/test-plans/04-portfolio-construction.md`
- PR draft path: `.ai/pr-description/04-portfolio-construction.md`

## Acceptance Criteria
- [x] AC1: The conviction-tilt optimiser produces a target portfolio where long weights sum to `target_long_gross` (default 0.90) and short weights sum to `target_short_gross` (default 0.60), with every individual position clamped to `[min_position_pct, max_position_pct]` (default 0.5%-5% of NAV).
- [x] AC2: The conviction-tilt optimiser applies a 1.5x weight multiplier to the top-5% scoring positions and a 1.25x multiplier to the top-10% scoring positions (within each book), then re-normalises each book to its gross target.
- [x] AC3: For any ticker with earnings within `earnings_blackout_days` (default 5) days, the optimiser halves the position weight and redistributes the surplus equally across remaining positions in the same book.
- [x] AC4: The liquidity cap limits each position to no more than `adv_max_pct` (default 5%) of its 20-day average daily volume; positions exceeding this cap are trimmed and the surplus is redistributed.
- [x] AC5: Sector neutrality is enforced so that the net sector weight (long minus short) for any GICS sector does not exceed `max_sector_net_pct` (default 5% of NAV); the portfolio net beta is kept within `max_beta` (default 0.15) by scaling the short book.
- [x] AC6: The MVO optimiser uses SLSQP to maximise `mu^T w - lambda * w^T Sigma w` subject to long/short gross targets, position bounds, beta constraint, and per-sector caps; if `result.success == False` or any exception occurs, it falls back to conviction-tilt and logs a warning.
- [x] AC7: The rebalance generator diffs the current `portfolio_positions` against the target, maps deltas to BUY/SELL/SHORT/COVER/HOLD actions, respects the `turnover_budget_pct` (default 30%) by trimming the smallest-delta-score trades first (full closures are never trimmed), and estimates transaction costs for each trade.
- [x] AC8: When `--rebalance` is run without `--whatif`, the system writes proposed trades to `position_approvals` (status PENDING), upserts `portfolio_positions`, and appends a snapshot row to `portfolio_history`.
- [x] AC9: The rebalance schedule checker returns advisory warning strings (never blocks execution) for: earnings within 2 days, FOMC meeting within 5 days, and options expiration within 3 days.
- [x] AC10: The entry point `run_portfolio.py` supports `--rebalance`, `--whatif`, `--current`, and `--optimize-method mvo|conviction` flags, and prints a portfolio summary including gross/net exposure, sector breakdown, and net beta.

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
