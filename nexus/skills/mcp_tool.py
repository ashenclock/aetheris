from typing import Any

from pydantic import BaseModel

from nexus.mcp import MCPClient, MCPServer

from .base_skill import BaseSkill


class MCPToolSkill(BaseSkill):
    """Expose one discovered MCP tool with approval required by default."""

    def __init__(self, server: MCPServer, tool: dict[str, Any], workspace_root):
        super().__init__(workspace_root)
        self.server = server
        self.tool = tool

    @property
    def name(self) -> str:
        return f"mcp__{self.server.name}__{self.tool['name']}"

    @property
    def description(self) -> str:
        return (
            f"Untrusted MCP tool from {self.server.name}: "
            f"{self.tool.get('description', self.tool['name'])}"
        )

    @property
    def requires_confirmation(self) -> bool:
        return True

    @property
    def parameters_schema(self) -> type[BaseModel]:
        return BaseModel

    def get_function_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.tool.get(
                "inputSchema", {"type": "object", "properties": {}}
            ),
        }

    async def execute(self, **kwargs: Any) -> str:
        return await MCPClient(self.server, str(self.workspace_root)).call_tool(
            self.tool["name"], kwargs
        )
