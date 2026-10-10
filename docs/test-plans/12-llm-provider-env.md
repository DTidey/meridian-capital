# Test Plan: llm-provider-env

Path: `docs/test-plans/12-llm-provider-env.md`

## What changed

- `analysis.llm.provider_settings` honours an `LLM_PROVIDER` env var over `llm.provider`.
- An autouse fixture in `tests/conftest.py` clears `LLM_PROVIDER` for every test.

## Acceptance criteria coverage

- AC1: `tests/test_llm.py::TestProviderSettings::test_env_var_overrides_config`,
  `test_env_var_overrides_missing_block`.

- AC2: `tests/test_llm.py::TestProviderSettings::test_blank_env_var_falls_back_to_config`,
  `test_unknown_env_provider_raises`. The existing packet 09 tests pass unchanged.

- AC3: `tests/conftest.py::_no_llm_provider_override` (autouse). Run `make test`, then
  `LLM_PROVIDER=openrouter pytest -q`; both pass with the same count.

- AC4: Review `config.yaml`, `.env.example`, `README.md` and `CLAUDE.md`. Manual check with
  `llm.provider: openai` committed and `LLM_PROVIDER=openrouter` in `.env`: a live call is routed
  to OpenRouter (`openai/gpt-4o-mini`).

## Commands

```bash
make lint
make test
LLM_PROVIDER=openrouter pytest -q
```
