# Changelog 2026-10-23

## Packet 46: factor monitor baseline

- Root cause of the nightly z-scores in the thousands: in `--whatif` runs `portfolio_positions`
  is empty, `compute_exposures` fills both books with 50.0, and the zero spread was compared with
  a universe top-half/bottom-half spread of about 50 that barely moves day to day.
- Baseline from `portfolio_history`, not from Layer 2's LONG/SHORT candidates. The candidates
  have 117 dates today, but about 100 equal-weighted names per side is not the 20/20 weighted
  book, so the level would differ and bias every z-score. Until packet 44 records a real book,
  the monitor reports no positions or insufficient history, which is accurate.
- One-sided books (today's or a past snapshot) are skipped: against the 50.0 default the spread
  is an artefact.
- Review: `portfolio_history` holds only each day's trades (`_update_positions` passes new and
  adjusted tickers to `save_positions`), so the baseline is like-for-like only once packet 44
  writes full books. Recorded on audit C10 instead of widening this packet.
- Review: a date's last commit is used whole, rather than the latest row per ticker, so a ticker
  closed by a second same-day commit does not leak back in.
- Numbered 46: 43 and 44 are reserved by the data audit plan.
