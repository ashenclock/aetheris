from pydantic import BaseModel, Field

from nexus.core.knowledge import KnowledgeStore

from .base_skill import BaseSkill


class RememberSchema(BaseModel):
    topic: str = Field(..., description="Short topic name for the durable note")
    content: str = Field(..., description="Concise knowledge worth preserving across sessions")


class RememberSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "remember"

    @property
    def description(self) -> str:
        return "Store durable project knowledge in a human-readable Markdown wiki."

    @property
    def parameters_schema(self) -> type[BaseModel]:
        return RememberSchema

    async def execute(self, **kwargs) -> str:
        path = KnowledgeStore().remember(kwargs["topic"], kwargs["content"])
        return f"Stored durable knowledge in {path}."
