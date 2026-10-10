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

## Edge cases
- From spec:
  - Env var with no `llm` block: `test_env_var_overrides_missing_block`
  - Blank or whitespace value: `test_blank_env_var_falls_back_to_config`
  - Unknown value: `test_unknown_env_provider_raises`
- Additional adversarial cases:
  - None present. The 2026-10-10 Reviewer pass found the four tests map one-to-one to the ACs.
    To be added in packet 14:
    - `LLM_PROVIDER=openai` with a committed OpenRouter `base_url`: the OpenAI key must not be
      sent to OpenRouter (exposes a latent defect)
    - Padded and mixed-case values (`" openrouter "`, `"OpenRouter"`)
    - End to end: an `OpenAIClient` built under the env override has the OpenRouter base URL,
      a namespaced model and JSON-mode `extra_body`

## Notes
- Flaky risks: a developer's `.env` leaking into tests; prevented by the autouse fixture.
- Determinism considerations: none.

## Commands

```bash
make lint
make test
LLM_PROVIDER=openrouter pytest -q
```
