import asyncio

from pydantic import BaseModel, Field

from nexus.web.search import WebSearchError, format_results, search_web

from .base_skill import BaseSkill


class WebSearchSchema(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    max_results: int = Field(5, ge=1, le=8)
    region: str = Field("us-en", min_length=2, max_length=12)
    timelimit: str | None = Field(None, description="d, w, m, or y")
    backend: str = Field("auto", description="DDGS backend or auto")
    domains: list[str] = Field(default_factory=list, max_length=5)


class WebSearchSkill(BaseSkill):
    """Run a bounded external search after an explicit user approval."""

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return (
            "Search the public web and return bounded title, URL, and snippet results."
        )

    @property
    def parameters_schema(self) -> type[BaseModel]:
        return WebSearchSchema

    @property
    def requires_confirmation(self) -> bool:
        return True

    async def execute(self, **kwargs) -> str:
        try:
            results = await asyncio.wait_for(
                asyncio.to_thread(search_web, **kwargs), timeout=15
            )
            return format_results(str(kwargs.get("query", "")), results)
        except asyncio.TimeoutError:
            return "Error: web search failed: timed out after 15 seconds"
        except WebSearchError as exc:
            return f"Error: web search failed: {exc}"
