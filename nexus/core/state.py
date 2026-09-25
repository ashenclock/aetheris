from __future__ import annotations

from enum import Enum
from typing import Optional

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
    max_steps: int = 40
    cost_budget_usd: float = 1.0
    consecutive_failures: int = 0
    last_action: Optional[str] = None
    last_error: Optional[str] = None
    checkpoint_count: int = 0

    def record_step(self, action: str, success: bool, error: str | None = None) -> None:
        self.step_count += 1
        self.last_action = action
        if success:
            self.consecutive_failures = 0
            self.last_error = None
        else:
            self.consecutive_failures += 1
            self.last_error = error

    def should_pause(self, total_cost_usd: float) -> bool:
        return self.step_count >= self.max_steps or total_cost_usd >= self.cost_budget_usd


class Checkpoint(BaseModel):
    state: TaskState
    reason: str = Field(min_length=1)
