## Summary

- Imports `AGENTS.md` and `10Rules.md` into `CLAUDE.md`. Claude Code only auto-loads
  `CLAUDE.md`, so packets 09-12 were made without those rules in context.
- Rewrites specs 09-12 to the spec template. The merged AC text is unchanged.
- Adds Edge cases (including adversarial) and Notes sections to test plans 09-12, based on a
  Reviewer pass on 2026-10-10.
- Records the non-obvious decisions for packets 10-13 in `log/changelog-2026-10-10.md`.

## Spec

- Spec: `docs/specs/13-agent-guidance-compliance.md`
- Test plan: `docs/test-plans/13-agent-guidance-compliance.md`
- PR draft path: `.ai/pr-description/13-agent-guidance-compliance.md`

## Acceptance Criteria

- [x] AC1: `CLAUDE.md` imports `AGENTS.md` and `10Rules.md`, and states that no-emoji covers
  attribution.
- [x] AC2: Specs 09-12 contain every template section; AC blocks are byte-identical to `main`.
- [x] AC3: Test plans 09-12 have Edge cases and Notes sections, listing present adversarial cases
  and Reviewer-identified gaps.
- [x] AC4: The decision log covers packets 10, 11, 12 and 13.

## Reviewer pass (packets 09-12)

No blockers. Important findings, deferred to packet 14:
- `dashboard/jarvis.py:274`: the stream loop indexes `choices[0]` without a guard, so a
  final usage-only chunk would raise. I didn't see one in a live OpenRouter check, but the schema
  allows it.
- `analysis/api_client.py:15`: every `APIStatusError` is retried, including permanent
  OpenRouter 402/404 errors, which costs about 62 s per call.
- `analysis/llm.py:27-29`: config `base_url` and `api_key_env` overrides apply whichever provider
  `LLM_PROVIDER` selects. That could send one provider's key to the other's host. It's latent; the
  shipped config doesn't set them.
- Packet 09 AC6 (call-site wiring) has no automated test.
- Packet 12 has no adversarial tests.

## Security Review

- [x] Security considerations reviewed in `docs/specs/13-agent-guidance-compliance.md`
- [x] Documentation and agent guidance only; no code paths change

## Validation

- Template-section and AC byte-comparison checks: pass
- `make lint`: pass
- `make test`: see CI

Open risks: the packet 14 findings above.
