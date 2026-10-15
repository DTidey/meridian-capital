# Test Plan: risk-extraction-display

Path: `docs/test-plans/33-risk-extraction-display.md`

## What changed
- `analysis/risk_analyzer.py`: `extract_risk_factors` accepts `Item 1.A`, `Item 1(a)` and
  "Part I"-prefixed headings and, when no Item 1A section is found, a bare "Risk Factors"
  heading ending at a later item's title.
- `dashboard/ticker_common.py`: `ai_score_text`, `ai_score_colour`; used by the AI score cards
  in `dashboard/page_ticker.py`.
- `analysis/report_generator.py`: the risk section shows the risk score and marks the overall
  label as not scored.

## Acceptance criteria coverage
- AC1: `tests/test_risk_extraction_display.py::TestItemHeadingVariants::test_item_1_dot_a`,
  `test_item_1_parenthesised_a`, `test_part_i_prefix`, `test_item_1_space_a`,
  `test_item_10_and_1a_ab_do_not_match`, `test_item_1a_still_ends_at_item_2`
- AC2: `TestItemHeadingVariants::test_item_1a_not_cut_by_subheading_titles`
- AC3: `TestBareRiskFactorsHeading::test_bare_heading_ends_at_later_item_title`,
  `test_bare_heading_upper_case_market_risk_end`, `test_mda_title_with_curly_apostrophe_ends_section`,
  `test_bare_heading_without_end_is_not_used`, `test_bare_used_when_item_1a_is_only_a_contents_entry`,
  `test_short_bare_section_rejected`, `test_heading_with_trailing_period`,
  `test_longest_bare_candidate_wins`
- AC4: `TestBareRiskFactorsHeading::test_item_1a_preferred_over_bare_heading`
- AC5: `TestTickerPageAiScores` (one decimal, missing, colour thresholds and boundaries)
- AC6: `TestReportRiskSection` (score and "not scored" label; no risks; no valid severity)
- Existing `tests/test_analyzer_inputs.py` extraction tests (packet 30) pass unchanged.

## Edge cases
- From spec: repeated page header, contents-only Item 1A, "Risk Factors Summary", cross
  references.
- Additional adversarial cases:
  - A bare section under 2,000 characters is rejected even with a valid end
  - A cross-reference sentence quoting "Risk Factors" followed by a later item title
  - A heading with a trailing full stop ("Risk Factors.", "Legal Proceedings.")

## Notes
- Tests written first: 8 of the first 18 failed on the previous code (the other 10 guard
  behaviour that must not change); `Item 1(a)` and "Part I" tests added after the universe
  check found those forms.
- Universe check (469 latest 10-Ks from EDGAR, old vs new extractor): section found for 448
  before, 463 after; 451 identical, 3 differ only by a page-header block. Details in the spec's
  decision log.

## Commands
```bash
make lint
make test
make security
```
