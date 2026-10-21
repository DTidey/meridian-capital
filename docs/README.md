# Documentation

## Living references (keep up to date)

Update these in the same PR as any change that makes them wrong.

| File | Covers |
|---|---|
| `specification.md` | What the system does, layer by layer; CLI flags; configuration; environment variables |
| `architecture.md` | Code layout, technology stack, Layer 1 internals, schema overview, test files |
| `erd.md` | Every table and column, and how they relate. Columns are generated from the SQLAlchemy definitions; regenerate rather than hand-edit |
| `run_all.md` | Common `run_all.py` invocations |
| `run_dashboard.md` | Starting the dashboard, its pages, and what it writes or calls |
| `alpaca_paper_trading.md` | Paper-trading walkthrough and switching to live |
| `data-audit-2026-10-21.md` | Sign-off checklist for the pre-paper-trading data audit; tick an item when its fix is verified in production |

The repository `README.md` is the entry point and should agree with these.

## Historical records (do not rewrite)

These record what was decided or built at the time. Later changes get a new packet rather than an
edit; where a later packet supersedes a definition, add a one-line pointer to it.

| Path | Contents |
|---|---|
| `specs/NN-slug.md` | One spec per change packet (the source of truth for that change) |
| `test-plans/NN-slug.md` | The matching test plan |
| `prompts/` | The original build prompts for each layer |
| `CHANGES_2026-06-15.md` | A dated change note from before the packet system |

Decision reasoning is kept in `../log/changelog-YYYY-MM-DD.md`; release history in
`../CHANGELOG.md`.
