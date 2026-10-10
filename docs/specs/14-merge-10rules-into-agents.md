# Merge 10Rules into AGENTS

**Spec file:** `docs/specs/14-merge-10rules-into-agents.md`
**Spec slug:** merge-10rules-into-agents
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-10

## Problem statement
- Agent rules were split across `AGENTS.md` and `10Rules.md`, and `AGENTS.md` never referred to
  `10Rules.md`.
- About a third of `10Rules.md` was missing from `AGENTS.md`. Tools that read only `AGENTS.md`
  (Codex, Cursor and others) never saw those rules. The gaps include surgical changes,
  failing-test-first, domain-specific caution, and verifying before claiming done.

## Scope
In scope:
- Adding every `10Rules.md` instruction missing from `AGENTS.md` to the matching existing
  section of `AGENTS.md`.
- Deleting `10Rules.md` and its `@10Rules.md` import in `CLAUDE.md`.

Out of scope / non-goals:
- New rules, or changing the meaning of existing ones.
- Editing historical packet docs (packet 13, logs, CHANGELOG history) that mention `10Rules.md`.
- The matching change in DTidey/aidev, which is a separate PR in that repo.

## Assumptions
- `AGENTS.md` is the conventional, tool-agnostic file, so it should be the single source of
  rules.
- The user confirmed deleting `10Rules.md` (2026-10-10).

## Proposed behavior / API
### Public interface
- No code interfaces change.
- `AGENTS.md`: additions in Non-Negotiable Rules, Coding Standards, and Style & Communication.
- `CLAUDE.md`: imports `@AGENTS.md` only.

### Inputs / outputs
- Inputs: not applicable.
- Outputs: documentation only.
- Error handling: not applicable.

### Examples
```markdown
- Test before trusting: for bugs, reproduce with a failing test first; ...
```

## Acceptance criteria
- AC1: Every instruction in the deleted `10Rules.md` appears in `AGENTS.md`, checked by
  key-phrase coverage of all 57 instructions.
- AC2: No existing `AGENTS.md` rule is removed or weakened; the diff only adds wording or
  sentences.
- AC3: `10Rules.md` is deleted, and no live file references it. `CLAUDE.md` imports only
  `@AGENTS.md`.

## Files changed

| File | Change |
|---|---|
| `AGENTS.md` | Missing 10Rules instructions merged into existing sections |
| `10Rules.md` | Deleted |
| `CLAUDE.md` | `@10Rules.md` import removed |

## Security considerations
- Auth/authz impact: None.
- Input handling or injection risk: None.
- Secrets or credential handling: None. The "never hardcode secrets" rule is retained.
- Data exposure or privacy impact: None.
- File system access impact: None.
- Network or external service impact: None.
- Dependency or supply-chain impact: None.
- Security notes for reviewers/testers: Confirm "Protect the system" and the destructive-deletion
  confirmation rule are both present in `AGENTS.md`.

## Edge cases
- A 10Rules instruction already partly present in `AGENTS.md`: extend the existing bullet rather
  than duplicating it.
- Wording differences between the files ("generic" vs "unnecessary" abstractions): keep both.

## Test guidance
- AC1 -> key-phrase coverage script (see the test plan)
- AC2 -> `git diff main -- AGENTS.md`: removed lines reappear extended, never shortened
- AC3 -> `grep -rn 10Rules` across live files; review `CLAUDE.md`

## Decision log
- 2026-10-10: Chose a merge into `AGENTS.md` over a one-line pointer, so tools that don't follow
  links still get every rule. 10Rules is deleted so the rules can't drift apart again.
- 2026-10-10: ASCII forms (`<=`, `->`) were kept, matching meridian's copy. aidev uses
  typographic forms (`≤`, `→`); the aidev PR will standardise on ASCII.
