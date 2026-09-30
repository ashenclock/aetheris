from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

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
    ) -> None:
        super().__init__(workspace_root)
        self.model_name = model_name
        self.db_path = db_path
        self.parent_session = parent_session
        self.cost_budget_usd = cost_budget_usd
        self._delegation_count = 0

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

        self._delegation_count += 1
        child_session = f"{self.parent_session}:subagent:{self._delegation_count}"
        child = Agent(
            model_name=self.model_name,
            db_path=self.db_path,
            session_id=child_session,
            max_steps=kwargs.get("max_steps", 6),
            cost_budget_usd=self.cost_budget_usd,
            workspace_root=self.workspace_root,
            enabled_skill_names={"list_directory", "read_file", "search_code", "recall"},
        )
        await child.init()
        try:
            answer = await child.chat(
                f"Act as a {kwargs.get('role', 'researcher')}. Work only on this bounded read-only task:\n{kwargs['task']}\n"
                "Return concise findings, file paths, evidence, and unresolved risks."
            )
            return f"Sub-agent {child_session} report:\n{answer}"
        finally:
            await child.close()
