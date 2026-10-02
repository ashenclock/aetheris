import os
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from pathlib import Path

import pytest

from nexus.core.agent import Agent
from nexus.mcp import (
    MCPClient,
    MCPConnectionError,
    MCPServer,
    configured_servers,
    discover_tools,
)
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
async def test_agent_loads_mcp_tools_only_for_full_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("AETHERIS_ENABLE_MCP", "1")
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
async def test_mcp_tool_is_approval_gated(tmp_path, monkeypatch):
    monkeypatch.setenv("AETHERIS_ENABLE_MCP", "1")
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


@pytest.mark.asyncio
async def test_agent_does_not_start_workspace_mcp_without_opt_in(tmp_path, monkeypatch):
    marker = tmp_path / "mcp-started"
    script = tmp_path / "mcp_marker.py"
    script.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('started')\n",
        encoding="utf-8",
    )
    add_mcp_server(tmp_path, "marker", f"{sys.executable} {script}")
    monkeypatch.delenv("AETHERIS_ENABLE_MCP", raising=False)

    agent = Agent(model_name="mock/offline", workspace_root=tmp_path)
    await agent.init()

    assert "mcp__marker__" not in " ".join(agent.skills)
    assert not marker.exists()
    await agent.close()


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group cleanup")
async def test_mcp_timeout_terminates_server_descendants(tmp_path):
    script = tmp_path / "mcp_hangs.py"
    pid_file = tmp_path / "child.pid"
    script.write_text(
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(60)'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    client = MCPClient(
        MCPServer("hanging", sys.executable, (str(script),), ()),
        str(tmp_path),
        timeout=0.2,
    )

    with pytest.raises(TimeoutError):
        await client.list_tools()

    child_pid = int(pid_file.read_text(encoding="utf-8"))
    for _ in range(50):
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        state = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(child_pid)],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        if not state or state.startswith("Z"):
            break
        time.sleep(0.02)
    else:
        pytest.fail(f"MCP child process {child_pid} survived cleanup")


@pytest.mark.asyncio
async def test_mcp_rejects_valid_json_with_wrong_protocol_shape(tmp_path):
    script = tmp_path / "mcp_malformed.py"
    script.write_text("import sys\nfor _ in sys.stdin: print('[]', flush=True)\n")
    client = MCPClient(
        MCPServer("malformed", sys.executable, (str(script),), ()), str(tmp_path)
    )

    with pytest.raises(MCPConnectionError, match="non-object message"):
        await client.list_tools()
