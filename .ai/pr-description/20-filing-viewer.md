## Summary

Lets you read any recent 10-K, 10-Q or 8-K (and the latest earnings release) on the Ticker page,
with an optional AI summary.

- **Document tab (no AI):** fetches the filing's original HTML from EDGAR and converts it to
  markdown with paragraphs and real tables. Stored text had all line breaks removed at
  ingestion, so the income statement was one run-on line; the converted tables match the
  filing exactly. Cached for 24 hours; falls back to the stored text if EDGAR can't be reached.
- **AI summary tab:** "Generate summary" streams a gpt-4o-mini summary (8-K: results, guidance,
  notable items, capital returns; 10-K/10-Q: overview, results, segments, liquidity, risk
  factors, outlook). Saved after the first run, with model, date and cost. Figures not found
  in the filing are listed under the summary.
- The earnings release moved from its own expander into the selector.
- Ingestion and the stored text the AI analysts use are unchanged.

## Spec

- Spec: `docs/specs/20-filing-viewer.md`
- Test plan: `docs/test-plans/20-filing-viewer.md`
- PR draft path: `.ai/pr-description/20-filing-viewer.md`

## Acceptance Criteria

- [x] AC1: Documents found from the EDGAR index page; only sec.gov Archives URLs.
- [x] AC2: HTML to markdown with aligned tables (colspan/rowspan grid, symbols joined per row,
  repeat columns dropped); text escaped.
- [x] AC3: Selector with release and filings; summary and document tabs; old expander removed.
- [x] AC4: Document tab cached 24 h (failures not cached), with stored-text fallback.
- [x] AC5: Saved summary shown, else a streamed summary is generated, saved and costed.
- [x] AC6: Prompts by form; source cut at 100k tokens with a caption.
- [x] AC7: Figures not found in the source are flagged.
- [x] AC8: Model output links removed and `$` escaped.

## Security Review

- [x] Security considerations reviewed in `docs/specs/20-filing-viewer.md`
- [x] Requests only to `https://www.sec.gov/Archives/` URLs built from validated filing URLs
- [x] No filing or model text reaches `unsafe_allow_html`
- [x] `beautifulsoup4` declared directly; pin unchanged (4.15.0)

## Validation

- `make lint`: pass
- `make security`: pass
- `make test`: 774 passed
- Live: AAPL 8-K, 10-Q and 10-K converted in under 0.5 s each; tables match the filings; a
  real 8-K summary took 3.1 s and cost $0.0008 with no unverified figures. JPM and XOM 10-Qs
  also checked after the table fixes.

Reviewer pass (fresh subagent): one blocker (year headers could sit over the wrong figures when
"$" appears on only some rows), fixed and tested; live JPM/XOM checks then led to `rowspan`
support. Important items (failed fetches cached, duplicate labels, silent partial failures,
untested error paths) fixed.

## Risks

- The dashboard server now makes outbound calls to sec.gov (on selection) and the LLM API (on
  button press).
- Filing text is sent to the LLM provider when a summary is generated (public data).
