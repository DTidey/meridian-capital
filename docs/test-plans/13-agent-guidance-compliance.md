# Test Plan: agent-guidance-compliance

Path: `docs/test-plans/13-agent-guidance-compliance.md`

## What changed
- `CLAUDE.md` imports `AGENTS.md` and `10Rules.md`.
- Specs 09-12 rewritten to the template; test plans 09-12 gained Edge cases and Notes sections.
- Decision log extended for packets 10-13.

## Acceptance criteria coverage
- AC1: Review `CLAUDE.md`. Manual check: in a new Claude Code session, ask about the no-emoji
  rule and the adversarial-case requirement.
- AC2: For each spec, check that every `## ` heading in `.ai/templates/spec_template.md` is
  present, and compare the `## Acceptance criteria` block with `git show main:<spec>`.
- AC3: Review test plans 09-12 for the Edge cases and Notes sections.
- AC4: Review `log/changelog-2026-10-10.md` for headings covering packets 10, 11, 12 and 13.

## Edge cases
- From spec:
  - AC text drift in merged specs: caught by the AC2 byte comparison
- Additional adversarial cases:
  - A template heading missing from one spec only: the AC2 check runs per spec
  - A `## Commands` section lost when inserting the new sections: checked by heading order
  - `make lint` and `make test` still pass, confirming no code changed

## Notes
- Flaky risks: none.
- Determinism considerations: none.

## Commands
```bash
for f in docs/specs/{09,10,11,12}-*.md; do
  grep '^## ' .ai/templates/spec_template.md | while read -r h; do
    grep -qxF "$h" "$f" || echo "MISSING in $f: $h"
  done
done
make lint
make test
```
