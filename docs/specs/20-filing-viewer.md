# Filing Viewer

**Spec file:** `docs/specs/20-filing-viewer.md`
**Spec slug:** filing-viewer
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-14

## Problem statement
- On the Ticker page the earnings release text is unreadable, and SEC filings are only links.
  The user asked to select a filing and read it, with an AI model making text readable.
- Root cause of the unreadable text: ingestion strips HTML tags and collapses all whitespace
  (`data/sec_data.py`, `data/transcripts.py`), so paragraphs and tables are lost. The stored
  AAPL Q3 2026 release has no line breaks; its income statement is one run-on line.
- AI reformatting of stored text can't cover long filings (a 10-K is about 440k characters,
  beyond gpt-4o-mini's output limit) and risks altering figures. The user chose a hybrid on
  2026-10-14: an exact document view converted from the EDGAR original, plus an AI summary
  (gpt-4o-mini) on request. Viewable forms: 10-K, 10-Q, 8-K.

## Scope
In scope:
- Fetch a filing's documents from EDGAR on demand and convert HTML to markdown (no AI).
- AI summary per filing, streamed, saved after the first run, figures checked against the
  source.
- A filing selector on the Ticker page; the earnings release is viewed the same way (its 8-K).

Out of scope / non-goals:
- Changing ingestion or the stored `content_text` / `content` (the AI analyzers use them).
- Form 4.
- A standing LLM budget for the dashboard (none exists; cost is recorded per summary).

## Assumptions
- Each filing folder has `{accession}-index.htm` with a `tableFile` table. Row 1 is the main
  document (its href may be `/ix?doc=/Archives/...` for inline XBRL), and exhibit rows carry
  their type (e.g. `EX-99.1`). Checked on AAPL's Q3 2026 8-K and 10-Q.
- EDGAR tables use `colspan` and put `$`, `(`, `)` and `%` in their own cells.
- gpt-4o-mini has a 128k-token context; summaries send at most 100k tokens of source.
- `beautifulsoup4` is already installed (via yfinance) and becomes a direct dependency.

## Proposed behavior / API
### Public interface
- New `data/edgar_docs.py`:
  - `filing_documents(session, filing_url, accession, form_type) -> list[tuple[str, str]]`:
    (label, URL) for the main document, plus EX-99 exhibits for an 8-K.
  - `html_to_markdown(html) -> str`.
  - `fetch_filing_markdown(filing_url, accession, form_type) -> str`.
- New `analysis/doc_summary.py`:
  - `summary_messages(form_type, text) -> list[dict]`
  - `truncate_tokens(text, max_tokens) -> tuple[str, bool]`
  - `stream_summary(cfg, form_type, text, on_usage) -> Iterator[str]`
  - `unverified_figures(summary, source) -> list[str]`
  - `clean_summary(text) -> str`
- New `dashboard/ticker_documents.py`: `render_document_viewer(engine, ticker, cfg)`.
- `dashboard/ticker_research.py`: the earnings release expander is replaced by the viewer.
- CLI entry points: none.

### Inputs / outputs
- Inputs: `sec_filings` (accession, URL, form), `earnings_transcripts.source_accession_no`,
  EDGAR HTML, the LLM API.
- Outputs: rendered markdown; saved summaries in `analysis_results` (analyzer `doc_summary`,
  artifact `accession`).
- Error handling:
  - EDGAR fetch fails: caption saying so, then the stored text in a read-only text area.
  - No LLM API key: summary button disabled, with a caption.
  - LLM error: caption with the error; nothing saved.

## Acceptance criteria
- AC1: `filing_documents` parses `{accession}-index.htm`. It returns the main document first,
  with `/ix?doc=` removed. For an 8-K it also returns each `EX-99*` exhibit, in order. It only
  returns URLs under `https://www.sec.gov/Archives/`, and only builds the index URL when
  `filing_url` starts with `https://www.sec.gov/Archives/` and the accession has the
  `NNNNNNNNNN-NN-NNNNNN` form.
- AC2: `html_to_markdown` drops `script`, `style`, `ix:header` and `ix:hidden`. Block elements
  become separate paragraphs. Each outermost table becomes a markdown table:
  - `colspan` and `rowspan` (each capped at 50) are expanded into a grid, with a spanned
    cell's text copied into every position it covers.
  - Within each row, `$`, `(`, `€` and `£` join the next value, and `)` and `%` join the
    previous one.
  - Empty columns, and columns whose non-empty cells only repeat a neighbouring column, are
    dropped. Spanned text is shown once (its first kept position).
  - A table with one remaining column is rendered as paragraphs.
  - All text is markdown-escaped, `$` included.
- AC3: In the "SEC FILINGS" section, a selectbox (option values are indices, so identical
  labels stay selectable) lists the filings in the table (newest first)
  plus "Earnings release - <quarter> <year> (<date>)" when the latest `earnings_transcripts`
  row has a `source_accession_no`. Choosing one shows "AI summary" and "Document" tabs. The
  separate earnings-release expander (packet 18 AC7) is removed.
- AC4: The Document tab shows `fetch_filing_markdown` output in a scrollable container,
  cached per filing for 24 hours (up to 16 filings); failures are not cached. A failed main
  document fails the fetch; a failed 8-K exhibit is shown as a "Couldn't fetch" note. If the
  fetch fails, it shows a caption and the stored text in a read-only text area.
- AC5: The AI summary tab shows a saved summary for the accession if one exists, with model,
  date and cost. Otherwise a "Generate summary" button streams one from
  `resolve_model(cfg, analysis.openai_model_cheap)` (temperature 0.2, at most 1,500 output
  tokens). On success it is saved with `AnalysisCache.set` (analyzer `doc_summary`, cost
  from `CostTracker.record`). The button is disabled when no LLM API key is set.
- AC6: Summary prompts differ by form. 8-K / earnings release: headline results, guidance,
  notable items, capital returns. 10-K / 10-Q: overview, results, segments, liquidity and
  capital returns, risk factors (new or changed), outlook. Both say to quote figures exactly as
  in the source, add no links, and invent nothing. The source is converted markdown cut to at
  most 100,000 tokens. When it is cut, a caption says the summary covers the first part only.
- AC7: `unverified_figures` returns each number in the summary that has 2 or more digits
  (ignoring years 1900-2099), and is not found as a whole number in the source after removing
  thousands separators from both (so "12" is not verified by "12.5"). Any such figures are listed in a warning caption under the summary.
- AC8: `clean_summary` escapes `$`, turns inline and reference-style links and images into
  their text, removes reference definitions, unwraps `<https://...>` autolinks, and is applied
  before rendering. Streamlit's default (no raw HTML) is kept. Residual: bare URLs may still be
  auto-linked by Streamlit's markdown.

## Security considerations
- Auth/authz impact: None (dashboard on a trusted network).
- Input handling or injection risk: EDGAR HTML and model output are outside input. Converted
  text is markdown-escaped (AC2). Model output has `$` escaped and links removed (AC8), and no
  raw HTML. HTML is parsed by BeautifulSoup with lxml's HTML parser, which doesn't resolve
  external entities.
- Secrets or credential handling: The LLM key is read through `analysis.llm` as elsewhere.
  `SEC_USER_AGENT` / `SEC_USER_EMAIL` are sent to sec.gov as in ingestion.
- Data exposure or privacy impact: Filing text (public) is sent to the LLM provider.
- File system access impact: None.
- Network or external service impact: The dashboard server now calls sec.gov (on selection,
  cached) and the LLM API (on button press only). Requests only go to
  `https://www.sec.gov/Archives/` URLs built from validated filing URLs (AC1).
- Dependency or supply-chain impact: `beautifulsoup4` declared directly; it is already
  installed and pinned at 4.15.0.
- Security notes for reviewers/testers: Check index hrefs can't redirect requests off
  sec.gov, and that no model or filing text reaches `unsafe_allow_html`.

## Edge cases
- 8-K with no EX-99 exhibit: main document only.
- Earnings release without `source_accession_no` (FMP source): not in the selector.
- Release 8-K also in the filings list: one cached summary (same accession).
- Nested tables: only the outermost is converted; inner text stays in its cells.
- Index page missing or not the expected format: fetch fails (AC4 fallback).

## Test guidance
- AC1, AC2 -> `tests/test_edgar_docs.py`
- AC5, AC6, AC7, AC8 -> `tests/test_doc_summary.py`
- AC3, AC4, AC5 -> `tests/test_page_ticker.py::TestDocumentViewer` (`AppTest`, network and LLM
  faked)

## Decision log
- 2026-10-14: Convert the EDGAR original rather than reformat stored text with AI. The
  numbers are exact, any length works, and it's free; AI is used for summaries. Chosen by the
  user from the options presented.
- 2026-10-14: Fetch on demand instead of changing ingestion, so analyzer inputs and their
  cache are unaffected and 38k stored filings needn't be re-downloaded.
- 2026-10-14: Table grid rules (span copies, per-row symbol joining, dropping repeat columns)
  replaced two earlier attempts. Per-row "$" merging misaligned AAPL's 10-Q. Merging columns
  with no row holding real content in both was found by the Reviewer to shift year headers
  when "$" appears on only some rows.
- 2026-10-14: Summaries saved in `analysis_results` via `AnalysisCache`, rather than a new
  table, so they reuse its TTL and cost columns.
- 2026-10-14 (Reviewer pass): one blocker, fixed. The column-merge rule could put year headers
  over the wrong figures (Reviewer repro now a test). Checking JPM and XOM 10-Qs live then
  showed row-spanned labels shifting the year row, so `rowspan` support was added. Also fixed:
  failed fetches no longer cached for 24 hours, duplicate labels selectable, failed main
  document treated as a failed fetch, partial summary cleared on error, error, truncation and
  provenance captions tested, more link forms removed, figure check lookahead tightened.
