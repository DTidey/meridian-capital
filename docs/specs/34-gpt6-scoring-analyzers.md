# GPT-6 for the Scoring Analyzers

**Spec file:** `docs/specs/34-gpt6-scoring-analyzers.md`
**Spec slug:** gpt6-scoring-analyzers
**Status:** Done
**Owner:** DTidey
**Date:** 2026-10-15

## Problem statement
Stage 3 of the GPT-6 plan agreed on 2026-10-14. Packet 29 moved the non-trading LLM uses to
GPT-6 and pinned the four scoring analyzers (`analysis.analyzer_models`) with a test until a
shadow test showed the switch was sound. Packets 30, 31 and 33 then fixed the analyzer inputs
and the risk score, so the shadow test could run on final inputs. It ran on 2026-10-15 (decision
log) and favours GPT-6.

## Scope
In scope:
- `config.yaml`: `analysis.analyzer_models` set to earnings `gpt-6-sol`, filing `gpt-6-luna`,
  risk `gpt-6-sol`, insider `gpt-6-luna`.
- The packet 29 pinning test updated to guard the new models.
- Living docs that name the analyzer models.

Out of scope / non-goals:
- Prompt changes, score formulas and composite weights.
- `cost_ceiling_usd` (a full run is estimated at $12-13 against the $25 ceiling).
- Deleting stored gpt-4o results: the cache is keyed by model, so they are not reused and expire
  under the normal TTL.

## Assumptions
- The analysis cache is model-aware (packet 28), so the first run after the switch recomputes
  every analysis with the new models. Covered by `tests/test_analyzer_cache_model.py`.
- Both GPT-6 models are already supported as reasoning models with prices (packets 28, 29).
- Analyzer calls are JSON-mode calls, which use the provider's default reasoning effort;
  `llm.reasoning_effort` applies only to free-text calls. The shadow test used the same path.

## Proposed behavior / API
### Public interface
- Configuration only: `analysis.analyzer_models`.

### Inputs / outputs
- Same analyzer inputs and output schemas; scores come from the new models.

## Acceptance criteria
- AC1: `analysis.analyzer_models` is earnings `gpt-6-sol`, filing `gpt-6-luna`, risk
  `gpt-6-sol`, insider `gpt-6-luna`, and a test pins these values.
- AC2: Every model in `analyzer_models`, `openai_model` and `openai_model_cheap` is treated as a
  reasoning model and has its own price in the cost table.
- AC3: Each analyzer looks up the cache with its configured model, so results stored under the
  old models are not reused (existing behaviour, still covered).
- AC4: README, `docs/architecture.md` and `docs/run_dashboard.md` name the new analyzer models,
  and no `config.yaml` comment names gpt-4o.

## Security considerations
- No meaningful security impact: model names in configuration only. Same provider, key and
  endpoints; the same filing, transcript and insider data is sent as before.

## Edge cases
- The first run after the switch recomputes every analysis, so it takes about twice as long as a
  normal first run (GPT-6 calls are slower) and costs a full run; `run_all.py` has no timeout.
- If a GPT-6 call fails after retries, the analyzer's existing error handling applies, as with
  gpt-4o.

## Test guidance
- AC1 -> `tests/test_config_models.py::test_scoring_analyzers_pinned`
- AC2 -> `tests/test_config_models.py::test_new_models_are_reasoning_and_priced`
- AC3 -> `tests/test_analyzer_cache_model.py::test_cache_lookup_uses_configured_model`
- AC4 -> review of the three docs (text change only)

## Decision log
- 2026-10-15: Shadow test. A seeded random sample of 50 of the 2026-10-14 candidates (25 LONG,
  25 SHORT) was run through the production analyzers twice, current models against GPT-6, with
  a stand-in cache, so both ran fresh and nothing was written. No errors. Cost $2.84 (current)
  and $3.12 (GPT-6).

  | Analyzer | Std dev current / GPT-6 | Rank agreement | Mean change | Median secs |
  |---|---|---|---|---|
  | Earnings (4o -> Sol) | 0.79 / 1.05 | 0.70 | -0.98 | 6.5 -> 11.3 |
  | Filing (4o-mini -> Luna) | 1.06 / 1.03 | 0.70 | -0.57 | 2.6 -> 12.3 |
  | Risk (4o -> Sol) | 0.50 / 0.30 | 0.08 | +0.08 | 7.1 -> 10.9 |
  | Insider (4o-mini -> Luna) | 2.51 / 1.69 | 0.50 | +1.66 | 1.7 -> 3.8 |

- 2026-10-15: Insider was the largest difference. gpt-4o-mini rated 21 of 31 tickers
  STRONG_SELL (score 1); Luna rated none STRONG_SELL (16 MODERATE_SELL, 14 NEUTRAL, 1
  STRONG_BUY). Reading the cases, Luna follows the prompt's instruction to separate routine from
  meaningful selling (e.g. it identified BEN's large seller as an entity rather than an
  executive, and flagged missing plan context for MSFT's CEO/CFO sales); gpt-4o-mini mostly
  equated any selling with a strong sell.
- 2026-10-15: Risk agrees poorly between models and Sol's scores are closer together, so risk
  carries less weight in the composite. Sol's risk score is the only one leaning the right way
  against one-year realised volatility (rank correlation -0.18, gpt-4o +0.04; 48 tickers, so
  suggestive only). All four switched, as agreed with the owner.
- 2026-10-15: Trading impact. AI composites on the sample: mean 6.31 -> 6.14, standard
  deviation 0.90 -> 0.66, rank correlation 0.77. Swapping the 50 into the 2026-10-14 universe
  changes the direction of 12 of 503 tickers, none LONG <-> SHORT; switching all ~200
  candidates is expected to change several times as many (estimate, not measured).
- 2026-10-15: Cost per analysed ticker $0.057 -> $0.062 (+10%); a full run of about 200
  candidates is estimated at $12-13.
- 2026-10-15 (Reviewer pass): no blockers or Important items; nothing in the analyzer path
  assumes gpt-4o behaviour. Stale `config.yaml` example fixed; code fallback defaults that
  still name gpt-4o (used only when a config key is missing) and a stale context-size comment
  logged in the backlog.
