## Summary

- Retries only temporary LLM API errors (408, 409, 429, >= 500), mirroring the openai SDK's own
  rule. Permanent errors such as 400, 402 (out of credits) and 404 fail on the first attempt,
  instead of after about 62 s of back-off. This applies to direct OpenAI too.
- Config `base_url` and `api_key_env` overrides now apply only to the provider they were written
  for, so `LLM_PROVIDER` can't send one provider's key to the other's host.
- Provider values are case-insensitive, and an unknown value's error names its source
  (`LLM_PROVIDER` or `llm.provider`).
- JARVIS streaming moved into `_stream_reply`, which skips chunks with empty `choices`.
  `render_chat` is back within the 50-line limit.
- Upstream cost is added only for BYOK requests (`is_byok`).
- Closes the adversarial-test gaps recorded in test plans 09 and 12, and adds wiring tests for
  packet 09 AC6.

## Spec

- Spec: `docs/specs/16-llm-review-findings.md`
- Test plan: `docs/test-plans/16-llm-review-findings.md`
- PR draft path: `.ai/pr-description/16-llm-review-findings.md`

## Acceptance Criteria

- [x] AC1: Only 408/409/429/>=500 are retried; other statuses raise on the first attempt.
- [x] AC2: Config overrides apply only when the effective provider matches `llm.provider`.
- [x] AC3: Provider values are stripped and lower-cased (blank config means `openai`); an unknown
  or non-string value's error names its source.
- [x] AC4: JARVIS streams via `_stream_reply`, which uses the factory and configured model and
  skips empty `choices`; `render_chat` is within 50 lines.
- [x] AC5: Upstream cost is excluded when `is_byok` is `False`; a cost of 0 records 0.0.
- [x] AC6: Letter and commentary wiring tested.
- [x] AC7: The recorded adversarial tests exist and pass.

## Security Review

- [x] Security considerations reviewed in `docs/specs/16-llm-review-findings.md`
- [x] Fixes a latent case where an API key could be sent to the wrong provider host

## Validation

- Tests written first: 18 failed for the expected reasons before the fixes
- `make lint`: pass
- `make security`: pass
- `make test`: 611 passed; also 611 with `LLM_PROVIDER=OpenRouter`
- Live: unknown model raises HTTP 400 in 0.2 s with no retry; JARVIS stream returns text

Reviewer pass (fresh subagent): no blockers. Two Important test gaps were fixed (invalid config
under an env override; key assertion in the reverse wrong-host case), plus blank and non-string
config handling.

Follow-ups (pre-existing, out of scope): OpenRouter mid-stream error chunks are treated as a
normal end of stream; a client error leaves an empty JARVIS bubble above the error message.

Open risks:
- Behaviour change: 4xx errors other than 408/409/429 are no longer retried on direct OpenAI
  either. They could never succeed on retry.
