# Changelog 2026-11-13

## Packet 66: per-trade pre-trade sector check, Layer 4 position cap

- **Root cause read from code, not logs.** The 2026-11-12 logs and database were not reachable
  from the worktree (no `.env`, no `output/`). The run report's reason text
  (`Industrials pro-forma gross 26.9% > 25%`) is reproduced exactly by the regression test, which
  is the evidence that the loop over every sector in `_check_sector_concentration` is the cause.
- **Where the 26.9% came from.** Since packet 44 the pro-forma book is the broker book with all
  PENDING targets applied, so 26.9% is the planned Industrials gross. The optimisers cap a
  sector per side (25% long, 25% short, 5% net), so they can plan a sector above 25% gross,
  which pre-trade then refuses. Not fixed here (a constraint change to both optimisers and spec
  04, and needs a decision on per-side vs gross); backlogged.
- **"Reduces gross" = the trade's own |weight| does not grow.** In the whole-book pro-forma the
  sector gross with the trade minus without it is exactly |target| - |current|, so comparing
  the trade's own weights is the same test without a second pass. SELL / COVER already skip
  check 5 as closing trades; the rule matters for flips (long to short) and any future
  non-closing reduction.
- **Cap in `generate_trades`, not in the optimisers.** The conviction-tilt optimiser lifts
  clipped names above 5% when it renormalises each book to its gross (about 5.05% for the top
  long at 20 names / 0.80 gross), and whole-share rounding adds up to one share after that.
  Rounding happens in `generate_trades`, so it is the only place a cap holds for both
  optimisers. The cost is a book a few tenths of a percent short of target gross on nights the
  cap binds, versus the trade being rejected outright before.
- **Cap = min(portfolio, pre-trade max_position_pct).** One value cannot drift from the other
  without the sizing following the stricter one; no new error path (packet 54 raises for the
  breaker cap because there a lower cap would force-close, here a lower cap just sizes down).
- **`not price > 0` instead of `price <= 0`.** A NaN close (half-written row, packet 63 test)
  passed `price <= 0` and reached `math.floor(nan)`.
- **Fixture configs set `max_position_pct` explicitly.** Twelve turnover/action tests use
  10-15% names with no cap configured; the code default is 5% in all three layers, so they now
  state a generous cap rather than the code special-casing a missing key.

## Packet 67: soupsieve advisories

- 4.0, not 2.10: pip-audit lists a different fix version per advisory (2.10 and 4.0). Auditing
  single-pin files showed 2.10 and 3.0.4 still carry CVE-2026-126190, so 4.0 is the lowest
  release that clears both.
- Pinned with `pip-compile -P soupsieve==4.0` on both `.in` files, so only the soupsieve line
  moved; beautifulsoup4 4.15.0 accepts any `soupsieve>=1.6.1`, so it stays.
- Tested in a throwaway venv under `/tmp`, not `.venv`, because `.venv` is shared with another
  running job.
- Numbered 67: 66 is in use on the open `hermes/fix-pretrade-sector-gross` branch.
