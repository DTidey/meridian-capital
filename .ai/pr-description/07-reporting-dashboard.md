## Summary
- Implements Layer 7, a nightly reporting engine that computes daily NAV series, decomposes P&L into four additive components (beta, sector, factor, alpha), performs FIFO lot matching for round-trip trade attribution, and writes an institutional-format tear sheet to `output/tear_sheet.md`.
- Adds JARVIS AI commentary (gpt-4o, weekly, cached in `weekly_commentary`) and daily LP letters (cached in `lp_letters`) with a full letterhead block, CONFIDENTIAL stamp, and compliance footer.
- Adds a six-page Streamlit dashboard (`dashboard/app.py`) covering Portfolio, Research, Risk, Performance, Execution, and Letter; auto-refreshes every 300 seconds during market hours (09:30-16:00 ET, weekdays).
- Provides win/loss analysis sliced by side, holding period, sector, VIX regime, and factor quintile; a tax estimate using FIFO-matched trades at short-term (37%) and long-term (20%) rates; and a Research page amber warning banner for earnings blackouts, FOMC proximity, and OpEx Fridays.

## Spec
- Spec: `docs/specs/07-reporting-dashboard.md`
- Test plan: `docs/test-plans/07-reporting-dashboard.md`
- PR draft path: `.ai/pr-description/07-reporting-dashboard.md`

## Acceptance Criteria
- [x] AC1: Running `python run_reporting.py` (no flags) sequentially executes nav_series, pnl_attribution, and position_attribution; all steps are idempotent (upsert on primary key; rerunning produces no duplicate rows).
- [x] AC2: `pnl_attribution.run()` decomposes each day's portfolio return into four additive components (beta_pnl, sector_pnl, factor_pnl, alpha_pnl) that sum to the total portfolio return within a floating-point tolerance of 1e-9.
- [x] AC3: `position_attribution.build_trades()` applies FIFO lot matching to `portfolio_history` snapshots and records entry date, exit date, realized P&L, and holding days for each closed round-trip in `position_trades`.
- [x] AC4: The Streamlit dashboard serves all six pages (Portfolio, Research, Risk, Performance, Execution, Letter) at `http://localhost:8502` and auto-refreshes every 300 seconds during market hours (09:30-16:00 ET, weekdays only).
- [x] AC5: The JARVIS commentary is generated via the `gpt-4o` model only on the configured weekday (default Friday) and is cached in `weekly_commentary`; subsequent calls on the same week return the cached content without a new API call.
- [x] AC6: The LP letter is generated via the `gpt-4o` model, cached by date in `lp_letters`, and rendered with a full letterhead block, CONFIDENTIAL stamp, JARVIS signature, and compliance footer; the Regenerate button forces a fresh API call and overwrites the cached row.
- [x] AC7: The tear sheet written to `output/tear_sheet.md` includes at minimum: performance vs SPY (Sharpe, Sortino, max drawdown, alpha), monthly returns grid, ASCII equity-curve sparkline, factor and sector exposure tables, turnover, and recent slippage stats.
- [x] AC8: The Research page (Page II) displays an amber warning banner when any held ticker has earnings within the configured blackout window, an FOMC meeting is within 3 days, or monthly OpEx Friday is within 3 days.
- [x] AC9: The tax estimate returned by `turnover.compute()` applies short-term rate 37% to gains on positions held <= 365 days and long-term rate 20% to gains on positions held > 365 days, derived from FIFO-matched `position_trades`.
- [x] AC10: `win_loss.compute()` returns win rate and P/L ratio sliced by side (LONG/SHORT), holding period bucket, sector, VIX regime at entry, and factor quintile at entry; an empty `position_trades` table returns zeroed dicts without raising an exception.

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
