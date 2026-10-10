# LLM Provider Environment Override

**Spec file:** `docs/specs/12-llm-provider-env.md`
**Status:** Done
**Date:** 2026-10-10

## Purpose

Packet 09 made the LLM provider selectable via `llm.provider` in `config.yaml`. That file is
committed and holds the fund's strategy parameters, so switching provider on one machine meant
keeping an uncommitted edit to it. Add an `LLM_PROVIDER` environment variable that overrides
`llm.provider`. It follows the existing `DATABASE_URL` pattern, so a machine's choice lives in
its `.env` and `config.yaml` stays identical to the repo.

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
- Secrets or credential handling: None. `LLM_PROVIDER` is not a secret, and keys are still read
  only from their own env vars.
- Network or external service impact: Setting `LLM_PROVIDER=openrouter` sends prompts to
  OpenRouter, as described in packet 09. The committed default stays `openai`.
