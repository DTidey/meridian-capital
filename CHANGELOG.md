# Changelog

This file records intentionally published project milestones.

Release policy:
- Development packet numbers like `01-data-ingestion` are not release numbers.
- Release versions use `MAJOR.MINOR.PATCH` formatting such as `0.1.0`.
- A new release section is created only when explicitly requested.
- Changes can accumulate under `Unreleased` until a release is intentionally cut.

## Unreleased

### Added
- Seven-layer pipeline: data ingestion, factor scoring, AI analysis, portfolio
  construction, risk management, order execution, reporting and dashboard.
- Initial spec-first workflow integration: specs `01`–`07` document all seven layers
  with acceptance criteria, companion test plans, and PR drafts.
- Selectable LLM provider (`llm.provider` in `config.yaml`): OpenAI direct or OpenRouter.
  All LLM call sites share one client factory; cost tracking uses OpenRouter's reported cost
  when present (packet `09-openrouter-provider`).
- `LLM_PROVIDER` env var overrides `llm.provider`, so a machine can switch provider via `.env`
  without editing `config.yaml` (packet `12-llm-provider-env`).

### Changed
- `CLAUDE.md` imports `AGENTS.md` and `10Rules.md`; specs and test plans 09-12 brought up to the
  `.ai/templates` structure after a Reviewer pass (packet `13-agent-guidance-compliance`).

### Security
- Raised indirect-dependency pins (anyio, gitpython, msgpack, pillow, soupsieve, urllib3,
  virtualenv) to clear pip-audit advisories (packet `10-dependency-security-bumps`).

### Fixed
- `make sync` works again with pip 26.2: pip-tools raised to 7.6.2 and upgraded alongside
  pip (packet `11-pip-tools-compat`).
