## Summary

Adds the data the Ticker page (page VII) didn't show. Stacked on #52 (packet 17), which fixes
the 13F data this page displays; merge #52 first.

- **Institutional holdings:** funds holding (n / 9 tracked), net share change and new positions
  for the latest quarter, plus a per-fund table with shares, value and change vs that fund's
  previous filing.
- **Signal:** combined, quant and AI scores, LONG/SHORT direction and regime.
- **Factor scores:** Institutional card added, plus a sub-factor table (36 columns).
- **AI analysis:** the latest write-up from each analyzer (earnings, filing, insider, risk) in
  expanders. Model text is escaped.
- **SEC filings:** latest 15 10-K/10-Q/8-K with EDGAR links, plus the latest earnings release
  text.
- **History:** price target / forward EPS and short interest charts; insider cluster flags.
- New sections live in three modules by domain; `page_ticker.py` only calls them.

## Spec

- Spec: `docs/specs/18-ticker-page-additions.md`
- Test plan: `docs/test-plans/18-ticker-page-additions.md`
- PR draft path: `.ai/pr-description/18-ticker-page-additions.md`

## Acceptance Criteria

- [x] AC1: Institutional summary cards for the latest quarter.
- [x] AC2: Per-fund table with carry-forward, change vs previous filing, sorted by value.
- [x] AC3: Signal section from `combined_scores` and regime.
- [x] AC4: Institutional card and sub-factor table.
- [x] AC5: AI analysis expanders with escaped model text.
- [x] AC6: 10-K/10-Q/8-K list with sec.gov-only links.
- [x] AC7: Latest earnings release in a read-only text area.
- [x] AC8: Estimate and short-interest history charts (two or more dates).
- [x] AC9: Insider cluster flags listed.
- [x] AC10: Page renders with an empty and a fully populated database (`AppTest`).

## Security Review

- [x] Security considerations reviewed in `docs/specs/18-ticker-page-additions.md`
- [x] Model and filing text is escaped or shown as plain text; no new `unsafe_allow_html` with
  database text
- [x] Links limited to `https://www.sec.gov/`

## Validation

- `make lint`: pass
- `make security`: pass
- `make test`: 701 passed
- Live render against production data (AAPL, CB, SPG): no exceptions, all sections present

## Risks

- Until the next 13F run after #52 merges, the institutional section shows the old rows.
- Bare `https://` URLs in model text are auto-linked by Streamlit markdown (inline markdown,
  HTML and `$` are escaped).
- Ticker matching by issuer name misses some quarters (about 3% of holdings), showing as false
  "New" rows. Setting `OPENFIGI_API_KEY` enables the existing CUSIP fallback; a fuller fix is a
  follow-up.

Reviewer pass (fresh subagent): two blockers (unescaped direction/regime in `metric_card`; no
AC8/AC9 tests) and three Important items fixed; see the spec's decision log.
