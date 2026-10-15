## Summary

Fixes the two backlog items packet 31 left behind.

- **Risk factors found in 15 more 10-Ks.** The extractor now accepts Item 1A headings written
  `Item 1.A` (CLX, ROL), `Item 1(a)` (HAL, ICE) and with a "Part I" prefix (EXPE, HIG). Only when
  no Item 1A section is found, it falls back to a bare "Risk Factors" heading (EIX, MS, HON,
  INTC, ...) that must end at a later item's title. Filings that already worked keep the same
  risk text.
- **Ticker page AI scores** show one decimal (packet 31's spread was invisible as whole
  numbers) and are coloured on the 1-10 scale. Before, every AI card showed the short colour
  because 1-10 scores were compared with the 0-100 thresholds.
- **Analysis report** leads with the risk score and marks the model's overall label "not
  scored".

## Spec

- Spec: `docs/specs/33-risk-extraction-display.md`
- Test plan: `docs/test-plans/33-risk-extraction-display.md`
- PR draft path: `.ai/pr-description/33-risk-extraction-display.md`

## Acceptance Criteria

- [x] AC1: Item 1A heading forms (`1A`, `1.A`, `1 A`, `1(a)`, "Part I" prefix) start the
  section; Item 1B/1C/2 in the same forms end it.
- [x] AC2: Item 1A sections end only at Item 1B/1C/2.
- [x] AC3: Bare "Risk Factors" fallback, only with a later item title as the end and at least
  2,000 characters.
- [x] AC4: An Item 1A section wins over a bare heading.
- [x] AC5: AI score cards show one decimal, coloured on the 1-10 scale.
- [x] AC6: Report shows the risk score and marks the overall label as not scored.

## Security Review

- [x] Security considerations reviewed in `docs/specs/33-risk-extraction-display.md` (no
  meaningful impact: text matching on filings already fetched, and display formatting)

## Validation

- Tests written first: 8 of the first 18 failed on the previous code
- `make lint`: pass
- `make security`: pass
- `make test`: 1035 passed
- Universe check, old vs new extractor on the latest 10-K of all 469 tickers that have one:
  section found for 448 before, 463 after; 451 identical; MLM, RMD and STZ differ only by a
  running page header at the section edge (same risk text). Each of the 15 new sections was
  checked to end at the right heading.

Reviewer pass (fresh subagent): no blockers. The bare-path early-end risk (a "Cybersecurity"
risk subheading would end a bare section) is documented as a known limit; added the suggested
tests.

## Risks and follow-ups

- The 15 newly found tickers keep their current cached risk result until it expires (up to 30
  days), a new 10-K arrives, or the packet 34 model switch recomputes everything.
- Six 10-Ks are still not matched (C, GE, NEM, SYF; USB and WFC point to the annual report
  exhibit). Backlog item.
