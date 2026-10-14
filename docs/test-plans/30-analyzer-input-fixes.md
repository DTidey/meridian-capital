# Test Plan: analyzer-input-fixes

Path: `docs/test-plans/30-analyzer-input-fixes.md`

## What changed
- `analysis/filing_analyzer.py`: DataFrame columns named in selection order.
- `analysis/risk_analyzer.py`: `extract_risk_factors`, `_risk_text`; `_fetch_10k` returns the
  filing URL.
- `analysis/cache.py`: filing and risk artifact ids end in `_v2`.

## Acceptance criteria coverage
- AC1: `tests/test_analyzer_inputs.py::TestFilingColumns` (distinct value per column; FIS
  regression)
- AC2: `TestExtractRiskFactors` (8 tests)
- AC3: `TestRiskText` (6 tests), `TestRiskAnalyzerPrompt`,
  `TestReviewerEdgeCases::test_toc_paragraph_only_falls_back`, `test_source_logged_on_success`
- AC4: `TestArtifactVersions`; updated `tests/test_analysis_cache.py::TestArtifactIds`

## Edge cases
- From spec: long sections cut, missing URL, heading not found.
- Additional adversarial cases:
  - Cross-reference sentence ("Item 1A. Risk Factors for a discussion of...") not a heading
  - Table-of-contents table not a heading
  - A short stray heading earlier doesn't beat the real, longer section
  - EDGAR fetch raising an exception falls back instead of failing the analysis
  - A short contents paragraph as the only match falls back (2,000-character minimum)
  - Em-dash headings; "Item 10" doesn't end the section; a short contents table before the real
    heading only prepends the contents rows

## Notes
- Live check (2026-10-14): Item 1A found in 67 of 72 real 10-Ks (shortest 23,941 characters,
  so none is affected by the 2,000-character minimum), including 30 of the 32 whose
  stored risk analysis saw no risk content; FIS's columns now correct (total assets $44.1B, CFO
  $494M).
- Shadow re-run on fixed inputs: see the spec's decision log.

## Commands
```bash
make lint
make test
```
