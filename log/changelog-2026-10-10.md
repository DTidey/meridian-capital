# 2026-10-10: OpenRouter provider (packet 09)

- **Kept the `openai` SDK, didn't add an OpenRouter SDK.** OpenRouter's API is OpenAI-compatible
  and the app only uses chat completions (JSON mode and streaming), so swapping `base_url` is
  enough. Retry logic built on `openai.RateLimitError` / `APIStatusError` keeps working.
- **Default remains `openai`.** Switching is a config change, and so is rolling back.
- **Bare model names auto-prefixed with `openai/` on OpenRouter.** This avoids editing six model
  strings when switching. It also means the analysis cache, which records the bare name, is
  unaffected.
- **Cost = `usage.cost` + `cost_details.upstream_inference_cost`.** `cost` is what OpenRouter
  charged. With bring-your-own-key it is only the OpenRouter fee, and the OpenAI bill shows up as
  upstream cost. Summing both keeps the $25 ceiling meaningful under either billing mode. The
  openai SDK keeps the unknown nested `cost_details` as a plain dict, not an object, so both forms
  are handled (a mock-only test missed this; there is now a test using a real `CompletionUsage`).
- **`require_parameters: true` for JSON mode only.** Free-text calls (letter, commentary, chat)
  don't need it and would just have fewer providers to route to.
- **Analysis cache isn't keyed on model.** If you change models (not just provider), run once with
  `--no-cache` or wait for the 30-day TTL. Otherwise results from the old model keep being served.
  Not changed in this packet.
- **Class name `OpenAIClient` kept** to avoid touching every analyser; it still wraps the OpenAI
  SDK.

# 2026-10-10: Dependency security bumps (packet 10)

- **Only the flagged pins were raised** (`pip-compile -P`), not a full `--upgrade`. That keeps the
  fix small enough to review, and leaves other bumps to Dependabot.
- **gitpython 3.2.0, not 3.1.51.** 3.1.51 clears the first set of advisories but has later ones,
  fixed in 3.1.54 to 3.1.57. pip-audit only reports those once the earlier ones are gone.
- **soupsieve held at 2.9.** An unconstrained upgrade went to 3.0.1, a major-version change to
  HTML parsing for a runtime scraping dependency.
- **virtualenv needed python-discovery to move too.** `-P virtualenv` alone stayed at 21.7.6,
  which is still vulnerable. It's dev-only, so it went to the latest version.

# 2026-10-10: pip-tools compatibility (packet 11)

- **Cause:** `make sync` upgrades pip but left pip-tools pinned, and pip-tools 7.5.3 imports a pip
  internal that pip 26.2 removed. CI hid it because `make compile` runs first and installs the
  latest pip-tools.
- **Fix both the pin and the Makefile.** Raising the pin alone fixes today's break. Upgrading
  pip-tools in `sync` stops the next pip release from breaking it again.

# 2026-10-10: LLM_PROVIDER env override (packet 12)

- **Env var, not a gitignored config.** `config.yaml` is mostly versioned strategy parameters
  (weights, risk limits) and is loaded by 15 modules, including test fixtures. Gitignoring it would
  lose that history and break CI and fresh clones. The env var matches `DATABASE_URL`.
- **Autouse fixture.** Tests that import entry points trigger `load_dotenv`, which would let a
  developer's `LLM_PROVIDER` change test results.
- **openai SDK 3.x.** Dependabot moved `openai` from 2.42 to 3.26 the same evening. CI only covers
  mocked calls, so JSON mode, reported cost (including BYOK upstream cost) and streaming were each
  checked with a live call.

# 2026-10-10: Agent guidance compliance (packet 13)

- **AGENTS.md wasn't loaded.** Claude Code auto-loads `CLAUDE.md` only. Importing `AGENTS.md` and
  `10Rules.md` there means the template, adversarial-test and no-emoji rules are in context every
  session.
- **Specs 09-12 were rewritten to the template with their AC text unchanged**, because the merged
  PRs ticked those exact ACs.
- **A packet, not a docs-only PR.** The validator treats `CLAUDE.md` and `log/` as code paths.

# 2026-10-10: Merge 10Rules into AGENTS (packet 14)

- **Merged, then deleted.** A one-line pointer would avoid duplication, but tools that don't
  follow links would still miss a third of the rules. Merging fully and deleting `10Rules.md`
  gives one source with nothing left to drift.
- **Extended existing bullets where rules overlapped**, rather than adding a separate
  "Ten Commandments" section that repeats most of AGENTS.md.
- **Packet numbering:** this took 14, so the Reviewer-pass code fixes move to 15.
