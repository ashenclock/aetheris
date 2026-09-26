from __future__ import annotations

import logging
import math
from typing import Any

from .state import TaskState

logger = logging.getLogger("AetherisTracker")


class CostTracker:
    """Track token totals and provider-reported cost estimates for one task."""

    def __init__(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.estimated_cost_usd: float | None = None
        self.cost_estimate_available = True

    def restore(self, state: TaskState) -> None:
        self.prompt_tokens = state.prompt_tokens
        self.completion_tokens = state.completion_tokens
        self.estimated_cost_usd = state.estimated_cost_usd
        self.cost_estimate_available = state.cost_estimate_available

    def add_usage(self, response: Any) -> dict[str, float | int | bool | None]:
        usage = getattr(response, "usage", None)
        self.prompt_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
        self.completion_tokens += int(getattr(usage, "completion_tokens", 0) or 0)

        if self.cost_estimate_available:
            try:
                import litellm

                cost = litellm.completion_cost(completion_response=response)
                if cost is None or not math.isfinite(float(cost)):
                    raise ValueError("provider did not return a finite cost estimate")
                if self.estimated_cost_usd is None:
                    self.estimated_cost_usd = 0.0
                self.estimated_cost_usd += float(cost)
            except Exception as exc:
                self.cost_estimate_available = False
                self.estimated_cost_usd = None
                logger.debug("Provider cost estimate unavailable: %s", exc)

        return self.summary()

    def summary(self) -> dict[str, float | int | bool | None]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "cost_estimate_available": self.cost_estimate_available,
        }
