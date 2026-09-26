from __future__ import annotations

from enum import Enum
from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskState(BaseModel):
    session_id: str
    goal: str
    status: TaskStatus = TaskStatus.RUNNING
    step_count: int = 0
    max_steps: int = Field(default=40, ge=1)
    cost_budget_usd: float = Field(default=1.0, gt=0)
    consecutive_failures: int = 0
    last_action: str | None = None
    last_error: str | None = None
    tool_calls: int = 0
    tool_failures: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_usd: float | None = None
    cost_estimate_available: bool = False
    recovered_interrupted_calls: int = 0
    human_review_pauses: int = 0
    checkpoint_count: int = 0

    def record_step(
        self,
        action: str,
        success: bool,
        error: str | None = None,
        *,
        is_tool_call: bool = True,
    ) -> None:
        self.step_count += 1
        self.last_action = action
        if is_tool_call:
            self.tool_calls += 1
        if success:
            self.consecutive_failures = 0
            self.last_error = None
        else:
            self.consecutive_failures += 1
            self.last_error = error
            if is_tool_call:
                self.tool_failures += 1

    def record_usage(self, summary: dict[str, float | int | bool | None]) -> None:
        self.prompt_tokens = int(summary["prompt_tokens"] or 0)
        self.completion_tokens = int(summary["completion_tokens"] or 0)
        cost = summary["estimated_cost_usd"]
        self.estimated_cost_usd = float(cost) if cost is not None else None
        self.cost_estimate_available = bool(summary["cost_estimate_available"])

    def should_pause(self) -> bool:
        step_limit_reached = self.step_count >= self.max_steps
        cost_limit_reached = (
            self.cost_estimate_available
            and self.estimated_cost_usd is not None
            and self.estimated_cost_usd >= self.cost_budget_usd
        )
        return step_limit_reached or cost_limit_reached


class Checkpoint(BaseModel):
    state: TaskState
    reason: str = Field(min_length=1)
