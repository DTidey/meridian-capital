# Analyzer Input Fixes

**Spec file:** `docs/specs/30-analyzer-input-fixes.md`
**Spec slug:** analyzer-input-fixes
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-14

## Problem statement
The stage 3 GPT-6 shadow test (2026-10-14) found two defects in what two AI analyzers send the
model. Both affect production AI scores (40% of the combined score) regardless of model.
1. **Filing analyzer: mislabelled columns (since the initial commit).** `_fetch_fundamentals`
   selects columns in `_FUND_COLS` order but names the DataFrame columns in table-definition
   order, so 7 of 14 are mislabelled (e.g. `cfo` values labelled `total_assets`, `total_assets`
   labelled `total_equity`, `total_equity` labelled `accounts_receivable`; three ratios
   shuffled). GPT-6 Luna flagged the result as impossible ("total assets $494M vs receivables
   $16B" for FIS); gpt-4o-mini scored it anyway. The stored data is correct.
2. **Risk analyzer: often never sees the risk factors.** It sends the first 80,000 characters of
   the stored 10-K text, which often ends before Item 1A. 32 of 277 stored risk analyses (12%)
   say the text contained no risk-factor content; e.g. REGN was scored 10 (lowest risk) on that
   basis. The stored text has whitespace collapsed and is cut at 500,000 characters, so the
   section can't be located reliably in it.

## Scope
In scope:
- Correct column labels in the filing analyzer.
- Risk analyzer text taken from the 10-K's Item 1A section, located in the EDGAR original
  (packet 20 converter), with a fallback.
- Invalidating stored filing and risk results so they are recomputed.

Out of scope / non-goals:
- Model choice (stage 3 decision follows a re-run of the shadow test on fixed inputs).
- Earnings and insider analyzers.

## Assumptions
- In `html_to_markdown` output, the Item 1A heading is a short block on its own (plain text or a
  one-row table, e.g. AMGN's `| Item 1A. | RISK FACTORS |`); table-of-contents entries are rows of
  longer multi-row tables, and cross-references are long sentences. Checked on A, AME, AMGN, BA,
  CB, REGN, AAPL, JPM on 2026-10-14.

## Proposed behavior / API
### Public interface
- `analysis.filing_analyzer._fetch_fundamentals`: unchanged signature.
- New `analysis.risk_analyzer.extract_risk_factors(markdown) -> str | None`.
- New `analysis.risk_analyzer._risk_text(filing, max_chars) -> tuple[str, str]` (text, source).
- `AnalysisCache.artifact_id_filing` / `artifact_id_risk`: keys gain a `_v2` suffix.

## Acceptance criteria
- AC1: Every column of the DataFrame `_fetch_fundamentals` returns holds the values of the
  database column of the same name.
- AC2: `extract_risk_factors` returns the text from the Item 1A heading block up to (not
  including) the next Item 1B, 1C or 2 heading block. A heading block is a paragraph that, with
  table pipes and dashes removed, is under 80 characters, starts "Item 1A" (or 1B/1C/2), and for
  1A mentions "risk factors". (Normalisation removes pipes, colons and runs of two or more
  dashes.) With several candidates, the longest section wins. It returns None when no heading is
  found.
- AC3: The risk analyzer sends that section, without markdown escapes, cut to
  `filing_risk_max_chars`. If the EDGAR fetch fails, no section is found, or the section is
  under 2,000 characters (a contents entry rather than the section), it falls back to the first
  `filing_risk_max_chars` of the stored text. It logs at INFO which source was used, on both
  paths.
- AC4: Filing and risk artifact ids end in `_v2`, so results stored under the old inputs are
  not served (they expire under the normal TTL; nothing is deleted).

## Security considerations
- Network: the risk analyzer now fetches the 10-K from `https://www.sec.gov/Archives/` via
  `data.edgar_docs` (packet 20's validated URLs) on a cache miss, about once per ticker per
  year plus a one-off recompute. Filing text is still sent to the LLM provider, as before.
- No new secrets or writes.

## Edge cases
- Very long risk sections (REGN about 213,000 characters): cut to `filing_risk_max_chars`.
- Filing URL missing or not on sec.gov: fallback to stored text.
- Headings in other styles (HON, HAL, VMC without "Item 1A"; NEM with a long subtitle): not
  matched; the stored-text fallback is used. Live check 2026-10-14: section found for 67 of 72
  10-Ks (30 of the 32 that previously had no risk content).

## Test guidance
- AC1 -> `tests/test_analyzer_inputs.py::TestFilingColumns`
- AC2 -> `tests/test_analyzer_inputs.py::TestExtractRiskFactors`
- AC3 -> `tests/test_analyzer_inputs.py::TestRiskText`
- AC4 -> `tests/test_analyzer_inputs.py::TestArtifactVersions`

## Decision log
- 2026-10-14: Locate Item 1A in the EDGAR original rather than the stored text: headings and
  table-of-contents rows are distinguishable there, and the 500,000-character cap doesn't apply.
- 2026-10-14: Version suffix on artifact ids instead of deleting cached rows: non-destructive,
  and old rows age out under the existing TTL.
- 2026-10-14: Shadow re-run on fixed inputs (50 tickers, both models fresh, $3.72): with the
  real risk-factors section, gpt-4o rates all 50 HIGH severity and GPT-6 46 of 50, so the
  three-level risk score barely differentiates; most of today's risk-score variation came from
  this bug (missing text scored low risk). Recorded as a follow-up (risk score design), not
  changed here.
- 2026-10-14: Measured effect of this packet alone (current models): filing scores average +2.09
  (rank correlation 0.40 with the old, mislabelled-input scores), risk -1.88; the AI composite
  barely moves on average (+0.05) because the two offset. With the 50 sampled tickers swapped in,
  16 of 503 tickers change direction and none flips between LONG and SHORT.
- 2026-10-14 (Reviewer pass): no blockers. Important: a short contents paragraph could pass as
  the heading and send a near-empty section; added the 2,000-character minimum (fallback) and
  logging of the source on both paths, plus tests for em dashes, "Item 10", the contents
  paragraph and a short contents table. Noted for the risk follow-up: the model gets only the
  prior 10-K's accession, not its text, so `new_risks` can't be grounded.
