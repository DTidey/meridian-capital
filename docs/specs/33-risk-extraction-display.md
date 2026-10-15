# Risk Factors Extraction and Risk Score Display

**Spec file:** `docs/specs/33-risk-extraction-display.md`
**Spec slug:** risk-extraction-display
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-15

## Problem statement
Two backlog items left by packet 31:
1. **Risk factors not found for some 10-Ks.** For CLX, EIX and MS the risk analyzer falls back
   to the start of the stored 10-K text, the model sees no risk factors, and since packet 31
   they get no risk score. Causes (checked on their latest 10-Ks, 2026-10-15):
   - CLX writes the heading "ITEM 1.A. RISK FACTORS"; the matcher requires "Item 1A". Other
     filings use "Item 1(a)" (HAL, ICE) or a "Part I." prefix (EXPE).
   - EIX ("RISK FACTORS") and MS ("Risk Factors") use a bare heading with no item number. Their
     sections end at the title of a later item: EIX at "QUANTITATIVE AND QUALITATIVE
     DISCLOSURES ABOUT MARKET RISK", MS at "Cybersecurity".
2. **Risk score display.**
   - The Ticker page shows AI scores (1-10 averages) as whole numbers, which hides most of the
     packet 31 spread (6.5 to 7.4 all read as 7).
   - Its card colours use the thresholds for 0-100 scores (60 and 40), so every AI score shows in
     the short colour.
   - The analysis report leads with "Overall severity", the model's label that no longer drives
     the score.

## Scope
In scope:
- `extract_risk_factors`: accept "Item 1.A" style headings, and fall back to a bare "Risk
  Factors" heading when no Item 1A section is found.
- Ticker page AI score cards: one decimal place, colours on the 1-10 scale.
- Report risk section: show the risk score and mark the overall label as not scored.

Out of scope / non-goals:
- Model changes (packet 34).
- Re-running analyses. The 15 newly found tickers keep their current cached risk result (built
  from the wrong text) until it expires (30 days), a new 10-K arrives, or packet 34's model
  switch recomputes all analyses. Clearing their cached rows sooner is an operator decision.
- The generic analysis expander on the Ticker page, which lists every stored field.
- The six 10-Ks still not matched (C, GE, NEM, SYF: other layouts; USB, WFC: risk factors
  incorporated by reference from the annual report exhibit). Backlog item.

## Assumptions
- Filings that already have an Item 1A section keep the same risk text: the bare-heading
  fallback runs only when no Item 1A section of at least 2,000 characters is found, and the Item
  1A path keeps its end rule (Item 1B, 1C or 2). Checked against every downloaded 10-K
  (decision log).
- A bare "Risk Factors" block can also be a running page header. Requiring a later item title
  as the end, plus the 2,000-character minimum, rejects those.

## Proposed behavior / API
### Public interface
- `analysis.risk_analyzer.extract_risk_factors(markdown) -> str | None`: same signature.
- New `dashboard.ticker_common.ai_score_text(val) -> str` and `ai_score_colour(val) -> str`.
- `analysis.report_generator._risk_section(result) -> list[str]`: same signature.

### Inputs / outputs
- Heading matching works on the normalised heading text, as before (`_heading`).
- Item 1A start: `Item 1A`, `Item 1.A`, `Item 1 A`, `Item 1(a)` (any case, optional "Part I"
  prefix) plus "risk factor". End: `Item 1B` / `1C` / `2` in the same forms.
- Bare start: the heading reads exactly "risk factors" (any case, trailing full stop allowed).
  End: an Item 1B/1C/2 heading, or a heading equal to one of: Unresolved Staff Comments,
  Cybersecurity, Properties, Legal Proceedings, Mine Safety Disclosures, Selected Financial
  Data, Quantitative and Qualitative Disclosures About Market Risk, Financial Statements and
  Supplementary Data; or one starting "Management's Discussion and Analysis" (straight or
  curly apostrophe).

## Acceptance criteria
- AC1: An Item 1A heading written `Item 1A`, `Item 1.A`, `Item 1 A` or `Item 1(a)`, optionally
  after a "Part I" prefix, with "risk factors" starts the section, ending at the next Item
  1B/1C/2 heading in any of those forms. `Item 10` and similar do not match.
- AC2: Within an Item 1A section, only Item 1B/1C/2 headings end it. Later-item titles such as
  "Cybersecurity" do not.
- AC3: When no Item 1A section of at least 2,000 characters exists, a bare "Risk Factors"
  heading starts the section, ending at the next later-item heading or title listed above. A
  bare candidate with no such end, or under 2,000 characters, is not used. Among candidates the
  longest wins.
- AC4: An Item 1A section of at least 2,000 characters is returned unchanged even if a bare
  heading also exists.
- AC5: Ticker page AI score cards show one decimal place ("—" when missing) and are coloured as
  long at 6 or above and short at 4 or below.
- AC6: The report's risk section shows "Risk score: x.x" (or "none" when no material risk has a
  valid severity), and shows the model's overall label marked "not scored" instead of "Overall
  severity".

## Security considerations
- No meaningful security impact: text matching on filings already fetched from sec.gov, and
  display formatting. No new inputs, network calls, secrets or dependencies. Report text still
  comes from stored model output, rendered as before.

## Edge cases
- Repeated page header "Risk Factors" without a later item title: rejected (AC3).
- Contents entry "Item 1A. Risk Factors" followed directly by "Item 1B": too short, so the bare
  fallback is tried (AC3).
- "Risk Factors Summary" heading: not a bare heading (exact match only).
- Known limit: in a bare section, a risk subheading that equals a later item title (e.g.
  "Cybersecurity", "Legal Proceedings") ends the section early. Those titles are needed as ends
  (MS, CAH, INTC and MCD end at "Cybersecurity"); the 15 newly found sections were checked by
  hand and none is cut short. Item 1A sections are not affected (AC2).
- Cross-reference sentences mentioning "Risk Factors": not headings (length rule, unchanged).

## Test guidance
- AC1 -> `tests/test_risk_extraction_display.py::TestItemHeadingVariants::test_item_1_dot_a`,
  `test_item_1_parenthesised_a`, `test_part_i_prefix`, `test_item_10_and_1a_ab_do_not_match`,
  `test_item_1a_still_ends_at_item_2`
- AC2 -> `TestItemHeadingVariants::test_item_1a_not_cut_by_subheading_titles`
- AC3 -> `TestBareRiskFactorsHeading` (bare heading ends, no end, short, contents-only Item
  1A, trailing full stop, summary heading, cross-reference)
- AC4 -> `TestBareRiskFactorsHeading::test_item_1a_preferred_over_bare_heading`
- AC5 -> `TestTickerPageAiScores`
- AC6 -> `TestReportRiskSection`

## Decision log
- 2026-10-15: Bare-heading fallback only when Item 1A fails, so the filings that work today
  keep exactly the same section.
- 2026-10-15: Later-item titles end only bare sections. Ending Item 1A sections at them would
  cut sections that have a "Cybersecurity" risk subheading.
- 2026-10-15: AI card colour fixed in the same packet: the cards were always the short colour
  because 1-10 scores were compared with 0-100 thresholds (found while changing the format).
- 2026-10-15: Universe check, old vs new extractor on the latest 10-K of every ticker with one
  (469 fetched from EDGAR; no model calls): section found for 448 before, 463 after. The 15
  newly found are CAH, CLX, EIX, EXPE, HAL, HIG, HON, ICE, ILMN, INTC, MCD, MS, ROL, VMC, WY;
  each ends at Item 1B/1C, Cybersecurity, Legal Proceedings or the market-risk item (checked
  one by one). 451 are byte-identical. MLM, RMD and STZ differ only by a running page header at
  the section's first or last block (the "Part I" prefix now matches it); the risk text is the
  same, so this is accepted.
- 2026-10-15: Still not found: C, GE, NEM, SYF (other layouts) and USB, WFC (their 10-K points
  to the annual report exhibit, which the analyzer does not fetch). Left to the backlog.
- 2026-10-15: Cached risk results are keyed by filing, so the newly found tickers are re-analysed
  when their cached result expires (30 days) or a new 10-K is filed, not immediately.
- 2026-10-15 (Reviewer pass): no blockers. Important: the bare-path early-end risk is now a
  documented known limit (edge cases); PR draft added. Tests added for `Item 1 A`, `Item 1AA`,
  the longest bare candidate, colour boundaries at 6 and 4, and the report line when no risk has
  a valid severity (wording changed to "no scorable material risks").
