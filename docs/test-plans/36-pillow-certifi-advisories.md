# Test Plan: pillow-certifi-advisories

Path: `docs/test-plans/36-pillow-certifi-advisories.md`

## What changed
- `requirements.txt`, `requirements-dev.txt`: certifi 2026.6.17 -> 2026.10.19.
- `Makefile`: `PILLOW_AUDIT_IGNORES` (eight pillow GHSA IDs) passed to pip-audit as
  `--ignore-vuln` in the `security` target.
- `tests/test_audit_ignores.py`: new.

## Acceptance criteria coverage
- AC1: `make security` ("No known vulnerabilities found, 8 ignored");
  `TestCertifi::test_certifi_at_fixed_version`
- AC2: `TestIgnoreList` (GHSA format, no duplicates, every ID listed in the spec, `security`
  target passes the variable, no other `--ignore-vuln`)
- AC3: `TestIgnoresAreTimeBoxed::test_no_ignores_once_pillow_13_is_pinned`
- AC4: `TestExposure::test_source_does_not_import_pil`
- AC5: `git diff main -- requirements*.txt` shows only the certifi lines; `make lint`,
  `make test`

## Edge cases
- From spec: pillow pins disagree between the two files (`test_pillow_pins_agree`); pillow 13
  pinned with ignores left (AC3).
- Additional adversarial cases:
  - Versions compare numerically, so 2026.9.1 counts as older than 2026.10.19
    (`test_versions_compare_numerically`)
  - Makefile parser follows backslash continuations and handles a single-line and an empty
    assignment (`TestMakefileParser`)
  - A blanket or extra `--ignore-vuln` added to the pip-audit line fails `test_no_blanket_ignores`
  - An unrelated GHSA ID added to `PILLOW_AUDIT_IGNORES` fails `test_ids_are_listed_in_spec`
  - The PIL scan fails if its source directories yield no files, so a rename cannot make it pass
    vacuously

## Notes
- Checked against `main`'s files: the ignore-list and certifi tests fail. With pillow set to
  13.0.0 in `requirements.txt`, the time-box test fails.

## Commands
```bash
make lint
make test
make security
```
