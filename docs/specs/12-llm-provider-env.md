# LLM Provider Environment Override

**Spec file:** `docs/specs/12-llm-provider-env.md`
**Spec slug:** llm-provider-env
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-10

## Problem statement
- Switching provider on one machine meant keeping an uncommitted edit to `config.yaml`.
- That file is committed, holds the fund's strategy parameters, and shouldn't carry
  machine-specific changes.

## Scope
In scope:
- An `LLM_PROVIDER` environment variable that overrides `llm.provider`.
- Keeping tests isolated from a developer's `.env`.
- Documentation of the variable.

Out of scope / non-goals:
- A general `config.local.yaml` overlay.
- Environment overrides for other config keys.
- Gitignoring `config.yaml`.

## Assumptions
- Entry points and the dashboard load `.env` via `load_dotenv`, so the variable reaches every
  LLM call site.
- Following the existing `DATABASE_URL` pattern is the least surprising choice.

## Proposed behavior / API
### Public interface
- `analysis.llm.provider_settings` reads `LLM_PROVIDER` before `llm.provider`.
- `tests/conftest.py`: autouse fixture `_no_llm_provider_override`.
- CLI entry points: none new.

### Inputs / outputs
- Inputs: `LLM_PROVIDER` (optional; `openai` | `openrouter`).
- Outputs: the provider settings in effect.
- Error handling: An unknown value raises the same `ValueError` as an unknown `llm.provider`. A
  blank or whitespace-only value is ignored.

### Examples
```bash
# .env
LLM_PROVIDER=openrouter
```

## Acceptance criteria

- AC1: When `LLM_PROVIDER` is set to a non-blank value, it overrides `llm.provider`, including
  when the `llm` block is absent. Provider defaults (base URL, key env var) follow the
  overriding provider.

- AC2: When `LLM_PROVIDER` is unset or blank, behaviour is unchanged from packet 09. An unknown
  value raises the same `ValueError` as an unknown `llm.provider`.

- AC3: The test suite isn't affected by a developer's `LLM_PROVIDER`, which entry-point imports
  load from `.env`. An autouse fixture clears it for every test, and the suite passes with
  `LLM_PROVIDER=openrouter` exported.

- AC4: `config.yaml`, `.env.example`, `README.md` and `CLAUDE.md` document the variable.

## Files changed

| File | Change |
|---|---|
| `analysis/llm.py` | `provider_settings` reads `LLM_PROVIDER` before `llm.provider` |
| `tests/conftest.py` | Autouse fixture clearing `LLM_PROVIDER` |
| `tests/test_llm.py` | Override, blank and unknown-value tests |
| `config.yaml`, `.env.example`, `README.md`, `CLAUDE.md` | Document `LLM_PROVIDER` |

## Security considerations
- Auth/authz impact: None.
- Input handling or injection risk: The value is only compared against the known providers;
  anything else raises.
- Secrets or credential handling: None. `LLM_PROVIDER` isn't a secret.
- Data exposure or privacy impact: Setting `openrouter` sends prompts to OpenRouter, as described
  in packet 09.
- File system access impact: None.
- Network or external service impact: As packet 09, chosen per machine.
- Dependency or supply-chain impact: None.
- Security notes for reviewers/testers: The committed default stays `openai`.

## Edge cases
- The variable is set but the `llm` block is absent.
- The variable is blank or whitespace.
- The variable has an unknown value.
- The developer's `.env` sets it during tests: the autouse fixture clears it.

## Test guidance
- AC1 -> `tests/test_llm.py::TestProviderSettings::test_env_var_overrides_config`,
  `test_env_var_overrides_missing_block`
- AC2 -> `test_blank_env_var_falls_back_to_config`, `test_unknown_env_provider_raises`
- AC3 -> `tests/conftest.py::_no_llm_provider_override`; `LLM_PROVIDER=openrouter pytest -q`
- AC4 -> docs review; live routing check

## Decision log
- 2026-10-10: Chose an env var over gitignoring `config.yaml`, because the file holds
  versioned strategy parameters. It also matches the `DATABASE_URL` pattern.
- 2026-10-10: Used an autouse fixture rather than per-test cleanup, because any test importing an
  entry point loads `.env`.
