from pydantic import BaseModel, Field

from nexus.core.knowledge import KnowledgeStore

from .base_skill import BaseSkill


class RememberSchema(BaseModel):
    topic: str = Field(..., description="Short topic name for the durable note")
    content: str = Field(
        ..., description="Concise knowledge worth preserving across sessions"
    )
    source: str | None = Field(
        None,
        description="Optional evidence such as a file path, command, or decision reference",
    )


class RememberSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "remember"

    @property
    def description(self) -> str:
        return "Store durable project knowledge in a human-readable Markdown wiki."

    @property
    def requires_confirmation(self) -> bool:
        """Treat curated wiki writes as a proposed memory update."""
        return True

    @property
    def parameters_schema(self) -> type[BaseModel]:
        return RememberSchema

    async def execute(self, **kwargs) -> str:
        root = (
            self.workspace_root / ".aetheris/wiki"
            if self.workspace_root
            else ".aetheris/wiki"
        )
        path = KnowledgeStore(root).remember(
            kwargs["topic"], kwargs["content"], kwargs.get("source")
        )
        return f"Stored durable knowledge in {path}."
