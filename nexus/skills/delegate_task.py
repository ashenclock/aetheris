from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from nexus.core.delegation import DelegationBudget, DelegationBudgetError

from .base_skill import BaseSkill


class DelegateTaskSchema(BaseModel):
    task: str = Field(..., min_length=1, description="A bounded read-only research task")
    role: str = Field("researcher", description="The sub-agent's narrow role")
    max_steps: int = Field(6, ge=1, le=12, description="Maximum steps for the child agent")


class DelegateTaskSkill(BaseSkill):
    """Run one bounded read-only child agent for long-horizon decomposition."""

    def __init__(
        self,
        workspace_root=None,
        *,
        model_name: str = "ollama/llama3",
        db_path: str = "aetheris_memory.db",
        parent_session: str = "default",
        cost_budget_usd: float = 0.25,
        subagent_model: str | None = None,
        max_children: int = 2,
        delegation_budget: DelegationBudget | None = None,
        context_char_budget: int = 12_000,
        parent_memory=None,
    ) -> None:
        super().__init__(workspace_root)
        self.model_name = model_name
        self.db_path = db_path
        self.parent_session = parent_session
        self.cost_budget_usd = cost_budget_usd
        self.subagent_model = subagent_model or model_name
        self.max_children = max_children
        self.delegation_budget = delegation_budget or DelegationBudget(
            cost_budget_usd, max_children
        )
        self.context_char_budget = context_char_budget
        self.parent_memory = parent_memory
        self.parent_state = None
        self._delegation_count = 0
        self.last_child_summary: dict[str, float | int | bool | None] | None = None

    @property
    def name(self) -> str:
        return "delegate_task"

    @property
    def description(self) -> str:
        return (
            "Delegate one bounded, read-only repository research task to a child agent. "
            "The child cannot delegate again or modify files."
        )

    @property
    def parameters_schema(self) -> type[BaseModel]:
        return DelegateTaskSchema

    async def execute(self, **kwargs: Any) -> str:
        from nexus.core.agent import Agent

        self.last_child_summary = None
        self.parent_state = kwargs.pop("_parent_state", None)
        requested_steps = int(kwargs.get("max_steps", 6))
        remaining_slots = max(1, self.max_children - self._delegation_count)
        requested_budget = min(
            self.cost_budget_usd,
            self.delegation_budget.total_usd / remaining_slots,
        )
        try:
            reservation = self.delegation_budget.reserve(requested_budget)
        except DelegationBudgetError as exc:
            return f"Error: child session was not started: {exc}."

        if self.parent_state is not None:
            self.parent_state.record_subagent_budget(
                self.delegation_budget.snapshot()
            )
            if self.parent_memory is not None:
                await self.parent_memory.checkpoint(
                    self.parent_state, "Reserved budget for child session."
                )

        self._delegation_count += 1
        child_session = f"{self.parent_session}:subagent:{self._delegation_count}"
        child = Agent(
            model_name=self.subagent_model,
            db_path=self.db_path,
            session_id=child_session,
            max_steps=min(requested_steps, 12),
            cost_budget_usd=reservation.amount_usd,
            workspace_root=self.workspace_root,
            enabled_skill_names={"list_directory", "read_file", "search_code", "recall"},
            context_char_budget=self.context_char_budget,
        )
        await child.init()
        try:
            answer = await child.chat(
                f"Act as a {kwargs.get('role', 'researcher')}. Work only on this bounded read-only task:\n{kwargs['task']}\n"
                "Return concise findings, file paths, evidence, and unresolved risks."
            )
            child_state = await child.memory.load_state()
            if child_state is not None:
                self.last_child_summary = {
                    "prompt_tokens": child_state.prompt_tokens,
                    "completion_tokens": child_state.completion_tokens,
                    "estimated_cost_usd": child_state.estimated_cost_usd,
                    "cost_estimate_available": child_state.cost_estimate_available,
                    "failed": child_state.status.value in {"failed", "paused"},
                }
                self.delegation_budget.settle(
                    reservation,
                    actual_cost_usd=child_state.estimated_cost_usd,
                    cost_available=child_state.cost_estimate_available,
                )
                if self.parent_state is not None:
                    self.parent_state.record_subagent_budget(
                        self.delegation_budget.snapshot()
                    )
            return f"Sub-agent {child_session} report:\n{answer}"
        except Exception:
            self.delegation_budget.settle(
                reservation, actual_cost_usd=None, cost_available=False
            )
            self.last_child_summary = {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "estimated_cost_usd": None,
                "cost_estimate_available": False,
                "failed": True,
            }
            raise
        finally:
            await child.close()
