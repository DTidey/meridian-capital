"""OpenAI SDK wrapper: retry, JSON mode, cost guard."""

import json
import logging
import time

import openai

from analysis.cost_tracker import CostCeilingExceeded, CostTracker

logger = logging.getLogger(__name__)

_RETRY_DELAYS = [2, 4, 8, 16, 32]   # seconds between retries
_RETRYABLE    = (openai.RateLimitError, openai.APIStatusError)


class OpenAIClient:
    """Thin wrapper around openai.OpenAI that adds retry, cost-guard, and JSON mode."""

    def __init__(
        self,
        api_key: str,
        model: str,
        cost_tracker: CostTracker,
    ) -> None:
        self._client  = openai.OpenAI(api_key=api_key)
        self._model   = model
        self._tracker = cost_tracker

    def chat(
        self,
        system_prompt: str,
        user_prompt:   str,
        model:         str | None = None,
        json_mode:     bool       = True,
    ) -> dict:
        """Call the chat completions endpoint and return a parsed dict.

        Args:
            system_prompt: Fixed instruction prompt (benefits from auto-caching).
            user_prompt:   Variable content (ticker-specific data).
            model:         Override the default model for this call.
            json_mode:     Use response_format={"type":"json_object"} (default True).

        Returns:
            Parsed JSON dict from the model response.

        Raises:
            CostCeilingExceeded: if cumulative spend is at or above the ceiling.
            openai.APIError: if all retries are exhausted.
        """
        effective_model = model or self._model

        if self._tracker.would_exceed_ceiling(model=effective_model):
            raise CostCeilingExceeded(
                f"Cost ceiling reached (${self._tracker.total_cost_usd():.2f}). "
                "Aborting further API calls."
            )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ]
        kwargs: dict = {"model": effective_model, "messages": messages}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        last_error: Exception | None = None
        for attempt, delay in enumerate([0] + _RETRY_DELAYS, start=1):
            if delay:
                logger.warning(
                    "Retry %d/%d after %.0fs (model=%s)",
                    attempt - 1, len(_RETRY_DELAYS), delay, effective_model,
                )
                time.sleep(delay)
            try:
                response = self._client.chat.completions.create(**kwargs)
                self._tracker.record(response.usage, effective_model)
                content = response.choices[0].message.content
                return json.loads(content)
            except _RETRYABLE as exc:
                # Rate limit or transient server error — retry
                last_error = exc
                logger.warning("OpenAI error (attempt %d): %s", attempt, exc)
            except json.JSONDecodeError as exc:
                # JSON mode should prevent this, but handle defensively
                logger.error("JSON decode error from model response: %s", exc)
                raise

        raise last_error  # type: ignore[misc]

    def estimate_tokens(self, system_prompt: str, user_prompt: str, model: str | None = None) -> int:
        """Count tokens in both prompts using tiktoken (no API call)."""
        import tiktoken
        effective_model = model or self._model
        try:
            enc = tiktoken.encoding_for_model(effective_model)
        except KeyError:
            enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(system_prompt)) + len(enc.encode(user_prompt))
