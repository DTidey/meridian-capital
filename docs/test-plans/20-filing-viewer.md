# Test Plan: filing-viewer

Path: `docs/test-plans/20-filing-viewer.md`

## What changed
- `data/edgar_docs.py` (new): finds a filing's documents from its EDGAR index page and converts
  HTML to markdown.
- `analysis/doc_summary.py` (new): summary prompts, token cut, streaming, figure check, output
  cleaning.
- `dashboard/ticker_documents.py` (new): filing selector with "AI summary" and "Document" tabs.
- `dashboard/ticker_research.py`: earnings-release expander removed (now in the viewer).
- `requirements.in`: `beautifulsoup4` declared (pin unchanged at 4.15.0).

## Acceptance criteria coverage
- AC1: `tests/test_edgar_docs.py::TestFilingDocuments` (8 tests)
- AC2: `tests/test_edgar_docs.py::TestHtmlToMarkdown` (10 tests), `TestTableLayouts` (4 tests,
  including the Reviewer's header-shift repro and a rowspan label)
- AC3: `tests/test_page_ticker.py::TestDocumentViewer::test_options_release_first_then_filings_no_form_4`,
  `test_release_uses_8k_folder_from_cik`
- AC4: `TestDocumentViewer::test_release_uses_8k_folder_from_cik` (document rendered),
  `test_fetch_failure_falls_back_to_stored_text`, `test_failed_fetch_not_cached`;
  `tests/test_edgar_docs.py::TestFetchFilingMarkdown` (3 tests)
- AC5: `TestDocumentViewer::test_generate_summary_saves_and_flags_figures`,
  `test_saved_summary_shown_without_button`, `test_button_disabled_without_key`,
  `test_llm_error_shows_caption_and_saves_nothing`, `test_saved_summary_provenance_caption`;
  `tests/test_doc_summary.py::TestStreamSummary`
- AC6: `tests/test_doc_summary.py::TestPrompts`, `TestTruncate`;
  `TestDocumentViewer::test_truncated_source_caption`
- AC3 duplicate labels: `TestDocumentViewer::test_same_label_filings_both_selectable` (drives the
  viewer directly; AppTest maps selections through labels)
- AC7: `tests/test_doc_summary.py::TestUnverifiedFigures` (6 tests)
- AC8: `tests/test_doc_summary.py::TestCleanSummary` (3 tests)

## Edge cases
- From spec:
  - 8-K without EX-99: `test_10q_main_document_only` covers main-document-only selection
  - Release without accession: not listed (smoke seed before packet 20 had none)
  - Nested tables: `test_nested_table_only_outer_converted`
  - Missing or malformed index: `test_missing_index_returns_empty`,
    `test_index_without_table_returns_empty`
- Additional adversarial cases:
  - Off-site hrefs in the index: `test_off_site_hrefs_ignored`
  - Non-sec.gov filing URL and malformed accession never fetched:
    `test_non_sec_filing_url_not_fetched`, `test_malformed_accession_not_fetched`
  - Hostile or invalid `colspan`: `test_huge_colspan_capped`, `test_bad_colspan_treated_as_one`
  - Markdown, HTML and `$` in filing text: `test_text_is_escaped`, `test_pipes_in_cells_escaped`
  - Number fragments inside longer numbers: `test_partial_number_match_not_accepted`
  - Links and images in model output: `test_links_and_images_become_text`
  - Usage-only stream chunks: `test_empty_choices_skipped`

## Notes
- Live check (2026-10-14), real EDGAR and gpt-4o-mini via the configured provider:
  - AAPL Q3 2026 8-K with EX-99.1: 15,485 chars of markdown in 0.4 s; 10-Q: 87,975 chars;
    FY2025 10-K: 214,405 chars (about 55k tokens, under the 100k cut).
  - Income statement and segment tables match the filing digit for digit.
  - After the Reviewer fixes, JPM and XOM 10-Qs also checked: year headers sit over their
    figures; JPM's footnote markers such as "(e)" keep their own narrow columns.
  - 8-K summary: 3.1 s, $0.0008, no unverified figures.
- Flaky risks: none in the suite (EDGAR and the LLM are faked; `st.cache_data` cleared per test).

## Commands
```bash
make lint
make security
make test
pytest tests/test_edgar_docs.py tests/test_doc_summary.py tests/test_page_ticker.py -q
```
