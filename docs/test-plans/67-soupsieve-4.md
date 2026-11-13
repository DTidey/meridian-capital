# Test Plan: soupsieve-4

Path: `docs/test-plans/67-soupsieve-4.md`

## What changed
- `requirements.txt`, `requirements-dev.txt`: soupsieve 2.9 -> 4.0.
- `tests/test_audit_ignores.py`: soupsieve pin tests, version-comparison and adversarial cases.

## Acceptance criteria coverage
- AC1: `make security` ("No known vulnerabilities found");
  `TestNoAuditIgnores::test_security_target_has_no_ignores`
- AC2: `TestPins::test_soupsieve_at_fixed_version`, `TestPins::test_soupsieve_pins_agree`
- AC3: `git diff origin/main -- requirements*.txt` shows only soupsieve; `make lint`;
  `make test` (includes `tests/test_edgar_docs.py`, which parses filing HTML through bs4)

## Edge cases
- From spec: pins drift between files (`test_soupsieve_pins_agree`); numeric version comparison
  (`TestVersionParsing::test_versions_compare_numerically`).
- Additional adversarial cases:
  - 2.10, which fixes only CVE-2026-126192, and 3.0.4 both fail the 4.0 floor; two-part `4.0`
    passes it (`TestVersionParsing::test_versions_compare_numerically`)
  - A lookalike package (`soupsieve-ext==4.0`) does not satisfy the soupsieve pin
    (`TestAdversarial::test_soupsieve_lookalike_does_not_count`)
  - A `soupsieve==4.0` inside a pip-compile `# via` comment does not count
    (`TestAdversarial::test_soupsieve_pin_in_via_comment_does_not_count`)

## Notes
- Checked against `main`'s pins: `test_soupsieve_at_fixed_version` fails there (2.9).
- The lowest fixing version was found by running pip-audit on single-line requirement files:
  2.10 and 3.0.4 still report CVE-2026-126190; 4.0 and 4.0.1 report nothing.
- pip-audit results depend on the advisory database on the day; a new advisory fails CI, which
  is intended.

## Commands
```bash
make lint
make test
make security
```
