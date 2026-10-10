# Agent Guidance Compliance

**Spec file:** `docs/specs/13-agent-guidance-compliance.md`
**Spec slug:** agent-guidance-compliance
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-10

## Problem statement
- Claude Code auto-loads `CLAUDE.md` but not `AGENTS.md` or `10Rules.md`. Packets 09-12 were
  produced without those rules in context.
- That left four gaps:
  - The specs don't follow `.ai/templates/spec_template.md`.
  - The test plans lack the Tester role's adversarial-case section.
  - There was no Reviewer pass.
  - The decision log covers only packet 09.

## Scope
In scope:
- Importing `AGENTS.md` and `10Rules.md` into `CLAUDE.md`.
- Rewriting specs 09-12 to the template, with their AC text unchanged.
- Adding Edge cases (from spec, plus adversarial) and Notes sections to test plans 09-12, using
  the 2026-10-10 Reviewer pass.
- Decision-log entries for packets 10-13.

Out of scope / non-goals:
- Code fixes for the Reviewer pass's Important findings. Packet 14 covers them, with the missing
  adversarial tests.
- Changes to `AGENTS.md`, `10Rules.md` or the `.ai/` templates (they match DTidey/aidev).
- Bringing specs 01-08 up to the template.

## Assumptions
- Claude Code resolves `@path` imports in `CLAUDE.md`.
- Changing the AC text of merged specs would break traceability to the PRs that ticked them, so
  it stays byte-for-byte unchanged.

## Proposed behavior / API
### Public interface
- No code interfaces change.
- `CLAUDE.md` gains a "Project rules" section importing `@AGENTS.md` and `@10Rules.md`.

### Inputs / outputs
- Inputs: not applicable.
- Outputs: documentation only.
- Error handling: not applicable.

### Examples
```markdown
## Project rules
@AGENTS.md
@10Rules.md
```

## Acceptance criteria
- AC1: `CLAUDE.md` imports `AGENTS.md` and `10Rules.md`, and states that the no-emoji rule applies
  to attribution.
- AC2: Specs 09-12 contain every section of `.ai/templates/spec_template.md`, and each
  `## Acceptance criteria` block is byte-for-byte identical to the merged version.
- AC3: Test plans 09-12 have Edge cases sections (from spec and additional adversarial) and Notes
  sections. Each one lists the adversarial cases present and the gaps found by the 2026-10-10
  Reviewer pass, or explains why adversarial cases don't apply.
- AC4: `log/changelog-2026-10-10.md` records the non-obvious decisions for packets 10, 11, 12
  and 13.

## Files changed

| File | Change |
|---|---|
| `CLAUDE.md` | Project rules section with imports |
| `docs/specs/09-12` | Rewritten to the spec template |
| `docs/test-plans/09-12` | Edge cases and Notes sections |
| `log/changelog-2026-10-10.md` | Entries for packets 10-13 |

## Security considerations
- Auth/authz impact: None.
- Input handling or injection risk: None.
- Secrets or credential handling: None.
- Data exposure or privacy impact: None.
- File system access impact: None.
- Network or external service impact: None.
- Dependency or supply-chain impact: None.
- Security notes for reviewers/testers: Documentation and agent guidance only; no code paths
  change.

## Edge cases
- Merged ACs edited by mistake: AC2 requires a byte-for-byte comparison with `main`.
- Packets with no meaningful adversarial tests (10, 11): the test plan says why.

## Test guidance
- AC1 -> review `CLAUDE.md`; start a new Claude Code session and confirm the rules are in context
- AC2 -> template section check, plus AC-block comparison against `main` (see the test plan)
- AC3 -> review the test plans
- AC4 -> review the log

## Decision log
- 2026-10-10: Packaged as a packet rather than a docs-only PR, because the validator treats
  `CLAUDE.md` and `log/` as code paths.
- 2026-10-10: The Reviewer pass's code findings were deferred to packet 14, so the fixes can follow
  test fails, then fix, then test passes.
