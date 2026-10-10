## Summary

- Adds an `LLM_PROVIDER` env var that overrides `llm.provider` in `config.yaml`, following the
  existing `DATABASE_URL` pattern. A machine picks its provider in `.env`, and the committed
  config, which holds the fund's strategy parameters, no longer needs a local edit.
- Adds an autouse test fixture that clears `LLM_PROVIDER`, so a developer's `.env` (loaded when
  tests import entry points) can't change test results.
- Documents the variable in `config.yaml`, `.env.example`, `README.md` and `CLAUDE.md`.

## Spec

- Spec: `docs/specs/12-llm-provider-env.md`
- Test plan: `docs/test-plans/12-llm-provider-env.md`
- PR draft path: `.ai/pr-description/12-llm-provider-env.md`

## Acceptance Criteria

- [x] AC1: A non-blank `LLM_PROVIDER` overrides `llm.provider`, including when the block is
  absent; provider defaults follow it.
- [x] AC2: Unset or blank leaves packet 09 behaviour unchanged; unknown values raise.
- [x] AC3: Tests are isolated from `LLM_PROVIDER`; the suite passes with it exported.
- [x] AC4: The variable is documented in config, `.env.example`, README and CLAUDE.md.

## Security Review

- [x] Security considerations reviewed in `docs/specs/12-llm-provider-env.md`
- [x] No new secrets; committed default remains `openai`

## Validation

- `make lint`: pass
- `make test`: 575 passed
- `LLM_PROVIDER=openrouter pytest -q`: 575 passed
- Live check: committed `provider: openai` plus `.env` `LLM_PROVIDER=openrouter` routes to OpenRouter
