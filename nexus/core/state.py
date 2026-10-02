from __future__ import annotations

from enum import Enum
from typing import Any
from pydantic import BaseModel, Field

MAX_RECENT_TOOL_SIGNATURES = 128


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
    pending_approval: dict[str, Any] | None = None
    recent_tool_signatures: list[str] = Field(
        default_factory=list, max_length=MAX_RECENT_TOOL_SIGNATURES
    )
    tool_calls: int = 0
    tool_failures: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_usd: float | None = None
    cost_estimate_available: bool = False
    recovered_interrupted_calls: int = 0
    human_review_pauses: int = 0
    checkpoint_count: int = 0
    subagent_sessions: int = 0
    subagent_failures: int = 0
    subagent_prompt_tokens: int = 0
    subagent_completion_tokens: int = 0
    subagent_estimated_cost_usd: float | None = None
    subagent_cost_estimate_available: bool = False
    subagent_budget_usd: float = 0.25
    subagent_budget_reserved_usd: float = 0.0
    subagent_budget_spent_usd: float = 0.0
    subagent_children_started: int = 0

    @property
    def status_label(self) -> str:
        """Explain that a final model turn does not verify the requested goal."""
        return (
            "model finished (goal unverified)"
            if self.status == TaskStatus.COMPLETED
            else self.status.value
        )

    def public_payload(self) -> dict[str, object]:
        """Serialize state with an explicit, derived completion interpretation."""
        payload = self.model_dump(mode="json")
        payload.pop("recent_tool_signatures", None)
        pending = payload.get("pending_approval")
        if pending:
            payload["pending_approval"] = {
                "id": pending["id"],
                "tool": pending["function"]["name"],
            }
        payload["status_label"] = self.status_label
        return payload

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
        failure_limit_reached = self.consecutive_failures >= 3
        cost_limit_reached = (
            self.cost_estimate_available
            and self.estimated_cost_usd is not None
            and self.estimated_cost_usd >= self.cost_budget_usd
        )
        return step_limit_reached or failure_limit_reached or cost_limit_reached

    def record_subagent(self, summary: dict[str, float | int | bool | None]) -> None:
        self.subagent_sessions += 1
        self.subagent_failures += int(summary.get("failed", False))
        self.subagent_prompt_tokens += int(summary.get("prompt_tokens", 0) or 0)
        self.subagent_completion_tokens += int(summary.get("completion_tokens", 0) or 0)
        cost = summary.get("estimated_cost_usd")
        if cost is not None:
            self.subagent_estimated_cost_usd = (
                self.subagent_estimated_cost_usd or 0.0
            ) + float(cost)
        self.subagent_cost_estimate_available = (
            self.subagent_cost_estimate_available
            or bool(summary.get("cost_estimate_available", False))
        )

    def record_subagent_budget(self, snapshot: dict[str, float | int]) -> None:
        self.subagent_budget_usd = float(
            snapshot.get("total_usd", self.subagent_budget_usd)
        )
        self.subagent_budget_reserved_usd = float(
            snapshot.get("reserved_usd", self.subagent_budget_reserved_usd)
        )
        self.subagent_budget_spent_usd = float(
            snapshot.get("spent_usd", self.subagent_budget_spent_usd)
        )
        self.subagent_children_started = int(
            snapshot.get("children_started", self.subagent_children_started)
        )


class Checkpoint(BaseModel):
    state: TaskState
    reason: str = Field(min_length=1)
