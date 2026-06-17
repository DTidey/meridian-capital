## Summary
- Implements a full 27 sub-factor scoring engine across 8 factor groups (momentum, value, quality, growth, revisions, short interest, insider, institutional) for every S&P 500 ticker
- Expresses all scores as 0-100 sector percentile ranks and produces a weighted composite with LONG/SHORT/NEUTRAL labels at the top and bottom quintiles
- Adds VIX-based regime-conditional weight adjustment and 60-day rolling crowding detection via pairwise factor return correlations
- Persists all scored results, regime state, and crowding flags to PostgreSQL and writes a daily CSV snapshot

## Spec
- Spec: `docs/specs/02-factor-scoring.md`
- Test plan: `docs/test-plans/02-factor-scoring.md`
- PR draft path: `.ai/pr-description/02-factor-scoring.md`

## Acceptance Criteria
- [x] AC1: The system computes 27 sub-factor scores across 8 factors (momentum 6, value 6, quality 8, growth 5, revisions 3, short interest 3, insider 2, institutional 3) for every ticker in the S&P 500 universe, expressed as sector-percentile ranks on a 0-100 scale.
- [x] AC2: Tickers with NaN raw values receive the sector median score (50.0) after ranking; if a sector has fewer than `min_sector_size` (default 5) non-NaN tickers for a sub-factor, the system falls back to universe-wide ranking and logs a warning.
- [x] AC3: The composite score is computed as a weighted sum of the 8 factor scores (weights from config, validated to sum to 1.0 at startup) and re-ranked within sector; tickers with composite >= 80 are labelled LONG and composite <= 20 are labelled SHORT.
- [x] AC4: The regime module reads the latest ^VIX close and adjusts factor weights — LOW_VOL (VIX < 15): momentum 0.28, value 0.10; HIGH_VOL (VIX > 25): quality 0.28, value 0.22, momentum 0.10 — re-normalising to sum to 1.0; if VIX data is unavailable, NORMAL weights are used with a logged warning.
- [x] AC5: The revisions factor defaults all three sub-factors to 50.0 when fewer than 30 days of `analyst_estimates` history exist for a ticker (degenerate mode), and logs a warning if more than 50% of the universe is degenerate.
- [x] AC6: The short interest factor stores LONG-convention scores (lower short interest = higher score); `composite.py` applies `100 - score` when computing the SHORT composite, without storing a separate column.
- [x] AC7: The insider factor weights CEO/CFO open-market transactions at 3x versus other insiders, and defaults both sub-factors to 50.0 when no open-market transactions (is_open_market = 1) exist in the 90-day window.
- [x] AC8: Crowding detection computes 60-day rolling pairwise Pearson correlations of factor return series and flags any pair where the deviation from its academic baseline exceeds 0.40; when fewer than 60 days of `factor_scores` history exist, the step is skipped with an informational log.
- [x] AC9: All scoring results, the resolved regime state, and crowding flags are upserted into `factor_scores`, `regime_state`, and `crowding_flags` tables and also written to `output/scored_universe_latest.csv`.
- [x] AC10: The entry point `run_scoring.py` accepts `--ticker`, `--date`, and `--no-crowding` flags and prints a structured summary including regime, LONG/SHORT counts, and degenerate factor warnings.

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
