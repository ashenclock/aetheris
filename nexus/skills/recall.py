from pydantic import BaseModel, Field

from nexus.core.knowledge import KnowledgeStore

from .base_skill import BaseSkill


class RecallSchema(BaseModel):
    query: str = Field(..., description="Text to search for in durable project knowledge")
    limit: int = Field(5, ge=1, le=10, description="Maximum number of matching pages")


class RecallSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "recall"

    @property
    def description(self) -> str:
        return "Search durable Markdown knowledge created by earlier sessions."

    @property
    def parameters_schema(self) -> type[BaseModel]:
        return RecallSchema

    async def execute(self, **kwargs) -> str:
        matches = KnowledgeStore().recall(kwargs["query"], kwargs.get("limit", 5))
        if not matches:
            return "No durable knowledge matched the query."
        return "\n\n".join(f"## {path.stem}\n{text}" for path, text in matches)
