"""Token and cost accounting for OpenAI API calls."""

import logging

logger = logging.getLogger(__name__)

# Pricing per 1M tokens (USD) as of mid-2025; update if rates change
_PRICING: dict[str, dict[str, float]] = {
    "gpt-4o": {
        "input": 2.50,
        "output": 10.00,
        "cached_input": 1.25,  # 50% off
    },
    "gpt-4o-mini": {
        "input": 0.15,
        "output": 0.60,
        "cached_input": 0.075,  # 50% off
    },
}

# Fall back to gpt-4o pricing for unknown models
_DEFAULT_PRICING = _PRICING["gpt-4o"]


class CostCeilingExceeded(Exception):
    """Raised when the cumulative cost would exceed the configured ceiling."""


class CostTracker:
    """Accumulates token usage and cost across multiple API calls in a run."""

    def __init__(self, ceiling_usd: float = 25.0) -> None:
        self._ceiling = ceiling_usd
        self._calls: list[dict] = []

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(self, usage, model: str) -> float:
        """Record usage from a completed API call; return incremental cost."""
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0

        # OpenAI SDK ≥ 1.x may expose cached_tokens inside prompt_tokens_details
        details = getattr(usage, "prompt_tokens_details", None)
        cached_tokens = getattr(details, "cached_tokens", 0) or 0
        regular_input = prompt_tokens - cached_tokens

        pricing = _PRICING.get(model, _DEFAULT_PRICING)
        cost = (
            regular_input * pricing["input"] / 1_000_000
            + cached_tokens * pricing["cached_input"] / 1_000_000
            + completion_tokens * pricing["output"] / 1_000_000
        )

        self._calls.append(
            {
                "model": model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cached_tokens": cached_tokens,
                "cost_usd": cost,
            }
        )
        logger.debug(
            "API call: model=%s prompt=%d completion=%d cached=%d cost=$%.4f",
            model,
            prompt_tokens,
            completion_tokens,
            cached_tokens,
            cost,
        )
        return cost

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def total_cost_usd(self) -> float:
        return sum(c["cost_usd"] for c in self._calls)

    def total_prompt_tokens(self) -> int:
        return sum(c["prompt_tokens"] for c in self._calls)

    def total_completion_tokens(self) -> int:
        return sum(c["completion_tokens"] for c in self._calls)

    def total_cached_tokens(self) -> int:
        return sum(c["cached_tokens"] for c in self._calls)

    def would_exceed_ceiling(self, estimated_tokens: int = 0, model: str = "gpt-4o") -> bool:
        """Return True if current spend (+ estimated next call) would exceed the ceiling."""
        pricing = _PRICING.get(model, _DEFAULT_PRICING)
        estimated_cost = estimated_tokens * pricing["input"] / 1_000_000
        return (self.total_cost_usd() + estimated_cost) >= self._ceiling

    def summary(self) -> dict:
        return {
            "calls": len(self._calls),
            "prompt_tokens": self.total_prompt_tokens(),
            "completion_tokens": self.total_completion_tokens(),
            "cached_tokens": self.total_cached_tokens(),
            "total_cost_usd": round(self.total_cost_usd(), 4),
            "ceiling_usd": self._ceiling,
        }

    # ------------------------------------------------------------------
    # Cost estimation (pre-flight, no API call)
    # ------------------------------------------------------------------

    @staticmethod
    def estimate_cost(token_count: int, model: str) -> float:
        """Estimate the cost of a single call with the given input token count."""
        pricing = _PRICING.get(model, _DEFAULT_PRICING)
        return token_count * pricing["input"] / 1_000_000
