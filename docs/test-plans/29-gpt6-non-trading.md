# Test Plan: gpt6-non-trading

Path: `docs/test-plans/29-gpt6-non-trading.md`

## What changed
- `config.yaml`: `openai_model: gpt-6-sol`, `openai_model_cheap: gpt-6-luna`.

## Acceptance criteria coverage
- AC1: `tests/test_config_models.py::test_non_trading_models_on_gpt6`
- AC2: `tests/test_config_models.py::test_scoring_analyzers_pinned`
- AC3: `tests/test_config_models.py::test_new_models_are_reasoning_and_priced`

## Edge cases
- Adversarial: a later config edit to `analyzer_models` fails `test_scoring_analyzers_pinned`
  until stage 3 updates it deliberately.
- Usage audit: every reader of the two keys checked (`grep`): JARVIS, LP letter, commentary,
  sector analysis (reports only), filing summaries; analyzer fallbacks unused because
  `analyzer_models` is explicit; `run_analysis.py` default client model is overridden per call.

## Notes
- Live checks (2026-10-14): Luna summary of AAPL's Q3 2026 8-K vs gpt-4o-mini, Sol JARVIS reply
  vs gpt-4o; see the spec.

## Commands
```bash
make lint
make test
```
