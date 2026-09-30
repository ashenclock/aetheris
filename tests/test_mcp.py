import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from pathlib import Path

import pytest

from nexus.core.agent import Agent
from nexus.mcp import MCPClient, configured_servers, discover_tools
from nexus.project import add_mcp_server


FIXTURE = str(Path(__file__).parent / "fixtures" / "mcp_demo_server.py")


@pytest.mark.asyncio
async def test_local_mcp_server_lists_and_calls_a_tool(tmp_path):
    add_mcp_server(tmp_path, "broken", f"{sys.executable} missing-server.py")
    add_mcp_server(tmp_path, "demo", f"{sys.executable} {FIXTURE}")
    servers = configured_servers(tmp_path)
    assert {server.name for server in servers} == {"broken", "demo"}
    client = MCPClient(
        next(server for server in servers if server.name == "demo"), str(tmp_path)
    )

    tools = await client.list_tools()
    assert tools[0]["name"] == "echo"
    assert await client.call_tool("echo", {"message": "ok"}) == "echo:ok"
    assert [
        (server.name, tool["name"]) for server, tool in await discover_tools(tmp_path)
    ] == [("demo", "echo")]


@pytest.mark.asyncio
async def test_agent_loads_mcp_tools_only_for_full_runtime(tmp_path):
    add_mcp_server(tmp_path, "demo", f"{sys.executable} {FIXTURE}")
    agent = Agent(model_name="mock/offline", workspace_root=tmp_path)
    await agent.init()
    assert "mcp__demo__echo" in agent.skills
    assert agent.skills["mcp__demo__echo"].requires_confirmation
    await agent.close()

    plan = Agent(model_name="mock/offline", workspace_root=tmp_path, mode="plan")
    await plan.init()
    assert not any(name.startswith("mcp__") for name in plan.skills)
    await plan.close()


@pytest.mark.asyncio
async def test_mcp_tool_is_approval_gated(tmp_path):
    add_mcp_server(tmp_path, "demo", f"{sys.executable} {FIXTURE}")
    agent = Agent(model_name="mock/offline", workspace_root=tmp_path)
    await agent.init()
    call = SimpleNamespace(
        id="mcp-1",
        function=SimpleNamespace(
            name="mcp__demo__echo", arguments='{"message":"hello"}'
        ),
    )
    state = await agent._ensure_state("test MCP approval")
    with patch.object(
        agent.skills["mcp__demo__echo"], "confirm", new=AsyncMock(return_value=False)
    ):
        await agent._execute_tool_call(
            {"id": call.id, "function": vars(call.function)},
            state,
        )
    history = await agent.memory.get_history()
    assert (
        "not been approved" in history[-1]["content"]
        or "not approved" in history[-1]["content"]
    )
    await agent.close()
