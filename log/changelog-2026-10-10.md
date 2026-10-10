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
