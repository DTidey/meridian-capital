## Summary

- Merges every `10Rules.md` instruction missing from `AGENTS.md` into the matching
  `AGENTS.md` section. Added: surgical changes, failing-test-first, domain-specific caution,
  verify-before-done, the protect-the-system side-effect list, small verifiable steps,
  "not by type", tight prose, junior-friendly terms, and clearing context between tasks.
- Deletes `10Rules.md` and its `CLAUDE.md` import, making `AGENTS.md` the single source of rules
  for every agent tool.
- No existing rule is removed or weakened.

## Spec

- Spec: `docs/specs/14-merge-10rules-into-agents.md`
- Test plan: `docs/test-plans/14-merge-10rules-into-agents.md`
- PR draft path: `.ai/pr-description/14-merge-10rules-into-agents.md`

## Acceptance Criteria

- [x] AC1: All 57 instructions from `10Rules.md` are present in `AGENTS.md` (key-phrase check).
- [x] AC2: No existing `AGENTS.md` rule is removed or weakened.
- [x] AC3: `10Rules.md` deleted; no live references; `CLAUDE.md` imports only `@AGENTS.md`.

## Security Review

- [x] Security considerations reviewed in `docs/specs/14-merge-10rules-into-agents.md`
- [x] Documentation only; secrets and destructive-deletion rules retained

## Validation

- Key-phrase coverage: 57/57
- `make lint`: pass
- `make test`: see below

Open risks: none. The matching change to DTidey/aidev follows as a separate PR.
