## Summary

- Adds `analysis/llm.py` and an `llm` block in `config.yaml` to select the LLM provider: OpenAI
  direct (default, unchanged behaviour) or OpenRouter via its OpenAI-compatible API.
- Routes every LLM call site through one client factory: Layer 3 analysers, LP letter, weekly
  commentary and JARVIS chat. JARVIS no longer hardcodes `gpt-4o`.
- On OpenRouter, bare model names are sent as `openai/<model>`, and JSON-mode requests are
  restricted to upstream providers that support `response_format`.
- `CostTracker` uses OpenRouter's reported per-call cost, including upstream cost on
  bring-your-own-key requests, so the cost ceiling stays accurate for any model.

## Spec

- Spec: `docs/specs/09-openrouter-provider.md`
- Test plan: `docs/test-plans/09-openrouter-provider.md`
- PR draft path: `.ai/pr-description/09-openrouter-provider.md`

## Acceptance Criteria

- [x] AC1: `llm.provider` selects the provider; absent or `openai` leaves behaviour unchanged;
  unknown provider raises.
- [x] AC2: OpenRouter clients use the OpenRouter base URL, `OPENROUTER_API_KEY` and the `X-Title`
  header; base URL and key env var are overridable.
- [x] AC3: Bare model names are namespaced as `openai/<model>` on OpenRouter.
- [x] AC4: JSON-mode requests on OpenRouter set `provider.require_parameters: true`.
- [x] AC5: Reported cost (plus BYOK upstream cost) is preferred over the pricing table, and the
  cost ceiling applies to it.
- [x] AC6: All call sites use the shared factory; JARVIS model comes from config; missing-key
  messages name the configured provider (log) or key env var (letter page).

## Security Review

- [x] Security considerations reviewed in `docs/specs/09-openrouter-provider.md`
- [x] New secret read from the environment only; never logged
- [x] Opting into OpenRouter sends prompts to a new third party (documented; off by default)

## Validation

- `make lint`: pass
- `make test`: 571 passed
