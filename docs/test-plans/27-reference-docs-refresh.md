# Test Plan: reference-docs-refresh

Path: `docs/test-plans/27-reference-docs-refresh.md`

## What changed
- `README.md`, `.env.example`, `docs/architecture.md`, `docs/erd.md`, `docs/specification.md`,
  `docs/run_dashboard.md`; new `docs/README.md`. Documentation only.

## Acceptance criteria coverage
- AC1: `grep -rnE "[0-9]{3} tests" README.md docs/*.md` returns nothing.
- AC2: a script parses each `erDiagram` block and compares its columns with
  `data.db.metadata` (after importing all layer `db` modules): 30 of 30 tables, no missing or extra
  columns.
- AC3-AC6: reviewed against `dashboard/app.py`, `dashboard/page_research.py` (approvals),
  `config.yaml`, `requirements.txt`, `run_data.py` and `execution/broker.py`.

## Edge cases
- Additional adversarial checks:
  - Stale numbers: grep for "505", "528", "$10M", "six pages", "six-page", "Claude" in the
    living docs returns nothing.
  - `ALPACA_PAPER` default confirmed in `execution/broker.py` (paper unless "false").
  - `run_data.py` step order in `architecture.md` matches the code's order.

## Notes
- No code changed; `make lint` and `make test` run as a sanity check.

## Commands
```bash
make lint
make test
grep -rnE "[0-9]{3} tests|\$10M|six pages|six-page|Claude" README.md docs/*.md
```
