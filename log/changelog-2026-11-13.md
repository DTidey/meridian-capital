# Changelog 2026-11-13

## Packet 66: per-trade pre-trade sector check, Layer 4 position cap

- **Root cause read from code, not logs.** The 2026-11-12 logs and database were not reachable
  from the worktree (no `.env`, no `output/`). The run report's reason text
  (`Industrials pro-forma gross 26.9% > 25%`) is reproduced exactly by the regression test, which
  is the evidence that the loop over every sector in `_check_sector_concentration` is the cause.
- **Where the 26.9% came from.** Since packet 44 the pro-forma book is the broker book with all
  PENDING targets applied, so 26.9% is the planned Industrials gross. The optimisers cap a
  sector per side (25% long, 25% short, 5% net), so they can plan a sector above 25% gross,
  which pre-trade then refuses. Not fixed in this first pass (a constraint change to both
  optimisers and spec 04, and needs a decision on per-side vs gross); fixed in the follow-up
  below once the owner confirmed combined gross.
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

## Packet 66, follow-up: optimisers cap sector combined gross (owner decisions)

- **The 25% is combined long + short gross (owner).** The single value is the existing
  `risk.pre_trade.max_sector_pct`, read through `portfolio/limits.py` `sector_gross_cap` by both
  optimisers, `generate_trades` and the pre-trade check. Reusing the pre-trade key means no
  config key moves and the check, already right, is untouched. `position_cap` moved to the same
  module so both shared limits live in one place.
- **Per-side caps kept.** At 0.25 per side and 0.25 combined the per-side cap is implied (a
  side cannot exceed its sector's gross), so it no longer binds in production; it still does if
  set lower, so removing it would be a behaviour change for no gain.
- **MVO: a linear constraint plus a post-solve check.** Infeasible problems (too few sectors for
  1.50 gross) already end in SLSQP failure and the existing conviction-tilt fallback. SLSQP can
  also report success with a small violation, so a sector more than 1e-6 over raises into the
  same fallback rather than reaching the trade list.
- **Conviction-tilt: scale both sides, refill elsewhere, then scale-down only after beta.**
  Scaling a breaching sector's long and short sides by one factor keeps its net within the net
  cap. Refilling from sectors with room keeps the books at their gross targets (the tests show
  0.80 / 0.70 kept for the 11-12 shape). The beta tilt can move weight back into a capped
  sector (a test with low-beta Utilities longs fails without the second pass); a refill after
  it could undo the beta limit, which pre-trade also enforces, so the second pass only removes
  exposure.
- **Whole-share sector guard in `generate_trades`.** The optimisers plan sectors at exactly
  25%, and rounding to the nearest share can tip that over by a few hundredths of a percent,
  which pre-trade would reject. Truncating toward zero only in sectors that end up over keeps
  every other target's rounding as before.
- **Owner decisions 2-4 recorded as deliberate**: no active trimming of an over-limit sector
  (the plan already targets it within the limit; reductions go through the normal turnover
  budget), all-or-nothing adds in an over-limit sector, and a small gross shortfall when a cap
  binds.
- **Fixtures.** Tests that put a whole book in one sector, or 1.50 gross across 5 sectors, and
  switched sector limits off with a per-side cap of 0.9-1.0 now also set the combined key:
  2.0 (off) where the intent was "never binds", 0.50 in `tests/test_optimizer.py` `_CONFIG`
  (per side 0.25, the most the old caps allowed) so its gross-target tests keep their meaning.

## Packet 67: soupsieve advisories

- 4.0, not 2.10: pip-audit lists a different fix version per advisory (2.10 and 4.0). Auditing
  single-pin files showed 2.10 and 3.0.4 still carry CVE-2026-126190, so 4.0 is the lowest
  release that clears both.
- Pinned with `pip-compile -P soupsieve==4.0` on both `.in` files, so only the soupsieve line
  moved; beautifulsoup4 4.15.0 accepts any `soupsieve>=1.6.1`, so it stays.
- Tested in a throwaway venv under `/tmp`, not `.venv`, because `.venv` is shared with another
  running job.
- Numbered 67: 66 is in use on the open `hermes/fix-pretrade-sector-gross` branch.
