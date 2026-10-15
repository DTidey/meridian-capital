# Backlog

Small items deferred for bundling into a future packet. When an item is
picked up, give it a spec in `docs/specs/` and remove it from this list.

## Risk factors still not found for six 10-Ks

- **Raised:** 2026-10-15, packet 33 universe check.
- **Problem:** C, GE, NEM and SYF use layouts the extractor does not recognise (no Item 1A
  heading, or "Risk Factors Relating to ..." sub-sections only). USB and WFC incorporate their
  risk factors by reference from the annual report exhibit (EX-13), which the analyzer does
  not fetch. All six fall back to the stored 10-K text.
- **Fix:** for USB/WFC, fetch the EX-13 exhibit when Item 1A points to it; for the others,
  inspect their layouts individually.

## Stale gpt-4o names in code defaults and comments

- **Raised:** 2026-10-15, packet 34 Reviewer pass.
- **Problem:** fallback defaults still name gpt-4o / gpt-4o-mini (`run_analysis.py`,
  `analysis/filing_analyzer.py`, `analysis/insider_analyzer.py`, `dashboard/jarvis.py`); they
  apply only when a config key is missing. `analysis/doc_summary.py` says "gpt-4o-mini context
  is 128k" though summaries use gpt-6-luna.
- **Fix:** point the defaults at the configured GPT-6 models (or require the keys) and correct
  the context comment against Luna's actual limit.
