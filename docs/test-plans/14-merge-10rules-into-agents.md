# Test Plan: merge-10rules-into-agents

Path: `docs/test-plans/14-merge-10rules-into-agents.md`

## What changed
- `AGENTS.md` now contains every `10Rules.md` instruction. `10Rules.md` and its `CLAUDE.md`
  import are removed.

## Acceptance criteria coverage
- AC1: Key-phrase coverage check: 57 phrases taken from each instruction of
  `git show main:10Rules.md` must appear in `AGENTS.md`. Result: 57/57.
- AC2: `git diff main -- AGENTS.md`. Each removed line is replaced by an extended version with
  the original wording intact. "Unnecessary abstractions" is kept alongside "generic".
- AC3: `grep -rn 10Rules --exclude-dir=.git --exclude-dir=.venv .` finds only historical packet
  13, log and CHANGELOG entries. `CLAUDE.md` contains `@AGENTS.md` only.

## Edge cases
- From spec:
  - Partly present rules are extended, not duplicated: diff review
- Additional adversarial cases:
  - A phrase present only in a historical doc, not in `AGENTS.md`: coverage runs against
    `AGENTS.md` alone
  - A 10Rules instruction split across two sentences: separate phrases for each part
  - `make lint` and `make test` pass, confirming no code changed

## Notes
- Flaky risks: none.
- Determinism considerations: none.

## Commands
```bash
grep -rn 10Rules --exclude-dir=.git --exclude-dir=.venv .
git diff main -- AGENTS.md CLAUDE.md
make lint
make test
```
