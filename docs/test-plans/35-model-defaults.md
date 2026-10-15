# Test Plan: model-defaults

Path: `docs/test-plans/35-model-defaults.md`

## What changed
- `analysis/llm.py`: `DEFAULT_MODEL` (`gpt-6-sol`), `DEFAULT_MODEL_CHEAP` (`gpt-6-luna`).
- Ten fallbacks use them: `run_analysis.py`, the four analyzers, `analysis/sector_analysis.py`,
  `analysis/doc_summary.py`, `reporting/commentary.py`, `reporting/lp_letter.py`,
  `dashboard/jarvis.py` (the last three inside their existing local imports).
- `analysis/doc_summary.py`: context-size and tokenizer comments corrected.
- `analysis/cost_tracker.py`: `would_exceed_ceiling(model=None)` prices as unknown (gpt-4o).

## Acceptance criteria coverage
- AC1: `tests/test_model_defaults.py::TestDefaultsMatchConfig` (2 tests)
- AC2: `tests/test_model_defaults.py::TestNoStaleLiterals`
- AC3: `tests/test_analyzer_cache_model.py::test_no_model_config_uses_code_defaults` (4 analyzers).
  The other six fallbacks (`run_analysis`, `sector_analysis`, `doc_summary`, `commentary`,
  `lp_letter`, `jarvis`) are covered indirectly: AC1 pins the constants and AC2 forbids literals.
- AC4: `tests/test_model_defaults.py::TestLazyImports::test_modules_do_not_load_openai_at_import`

## Edge cases
- From spec: config with only `analyzer_models` (existing `test_cache_lookup_uses_configured_model`),
  `would_exceed_ceiling()` without a model (`TestCeilingDefault`).
- Additional adversarial cases:
  - `would_exceed_ceiling` with no model reaches the ceiling at exactly the gpt-4o input price
    (`test_would_exceed_ceiling_default_uses_unknown_model_price`)
  - The literal scan covers every source directory and `run_*.py`, so a new gpt-4o fallback
    anywhere fails the build
  - A model change in `config.yaml` without updating the constants fails AC1

## Notes
- Tests written first: the new module failed to import before the constants existed.

## Commands
```bash
make lint
make test
make security
```
