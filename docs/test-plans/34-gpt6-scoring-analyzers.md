# Test Plan: gpt6-scoring-analyzers

Path: `docs/test-plans/34-gpt6-scoring-analyzers.md`

## What changed
- `config.yaml`: `analysis.analyzer_models` moved to GPT-6 (Sol for earnings and risk, Luna for
  filing and insider); comment updated.
- `tests/test_config_models.py`: the pinning test guards the new models; the reasoning/price
  test covers every configured model.
- `README.md`, `docs/architecture.md`, `docs/run_dashboard.md`: model names.

## Acceptance criteria coverage
- AC1: `tests/test_config_models.py::test_scoring_analyzers_pinned`
- AC2: `tests/test_config_models.py::test_new_models_are_reasoning_and_priced` (now covers the
  analyzer models too)
- AC3: `tests/test_analyzer_cache_model.py::test_cache_lookup_uses_configured_model`
  (unchanged; proves old-model results are not reused)
- AC4: doc review

## Edge cases
- From spec: slower first run after the switch, API failures.
- Additional adversarial cases (enforced by the assertions of the two updated tests, not by
  separate tests, since the change is configuration only):
  - The pinning test fails if any one analyzer is moved alone (dict equality)
  - A model added to `analyzer_models` without a price entry fails the price check
  - A non-reasoning model added to `analyzer_models` fails the reasoning check

## Notes
- Tests written first: both updated tests failed against the old config.
- The decision rests on the shadow test (50 tickers, both model sets, production analyzers),
  recorded in the spec's decision log; the script ran outside the repo.

## Commands
```bash
make lint
make test
make security
```
