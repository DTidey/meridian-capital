# Test Plan: dependency-security-bumps

Path: `docs/test-plans/10-dependency-security-bumps.md`

## What changed

- Raised pins for anyio, gitpython, msgpack, pillow, soupsieve and urllib3 in both lock files,
  and virtualenv and python-discovery in `requirements-dev.txt`, to clear pip-audit advisories.

## Acceptance criteria coverage

- AC1: `make security`. pip-audit reports "No known vulnerabilities found"; bandit is clean.

- AC2: Review `git diff main -- requirements.txt requirements-dev.txt`. Only the listed version
  lines change.

- AC3: Install the new pins (`pip install -r requirements.txt -r requirements-dev.txt`), then
  run `make test` (full suite) and `make lint`. Manual check: BeautifulSoup CSS selection works
  with soupsieve 2.9, and the Streamlit dashboard modules import with gitpython 3.2.0.

## Edge cases
- From spec:
  - First fixed version still has later advisories (gitpython): pip-audit re-run after each change
  - A package that needs a dependency bumped too (virtualenv with python-discovery)
- Additional adversarial cases:
  - Not applicable: lock-file-only change with no new code paths. Covered by pip-audit plus the
    full suite on the new versions, and by BeautifulSoup selector and dashboard import checks.

## Notes
- Flaky risks: pip-audit results change as new advisories are published.
- Determinism considerations: none.

## Commands

```bash
. .venv/bin/activate && pip install -r requirements.txt -r requirements-dev.txt
make security
make lint
make test
```
