"""Small, dependency-free MCP stdio client for trusted local servers."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from nexus.project import load_mcp_config


@dataclass(frozen=True)
class MCPServer:
    name: str
    command: str
    args: tuple[str, ...]
    env_names: tuple[str, ...]


def configured_servers(workspace: str) -> list[MCPServer]:
    """Read enabled project-local stdio servers from `.aetheris/mcp.json`."""
    config = load_mcp_config(workspace)
    return [
        MCPServer(
            name=name,
            command=str(item["command"]),
            args=tuple(map(str, item.get("args", []))),
            env_names=tuple(map(str, item.get("env", []))),
        )
        for name, item in config.get("mcpServers", {}).items()
        if item.get("enabled", True)
    ]


class MCPConnectionError(RuntimeError):
    """Raised when a local MCP server cannot complete the JSON-RPC handshake."""

    pass


class MCPClient:
    """Run one short-lived local MCP stdio session for discovery or a call."""

    def __init__(self, server: MCPServer, workspace: str, timeout: float = 10.0):
        self.server = server
        self.workspace = workspace
        self.timeout = timeout
        self._next_id = 0

    def _environment(self) -> dict[str, str]:
        allowed = {"PATH", "PYTHONPATH", *self.server.env_names}
        return {key: value for key, value in os.environ.items() if key in allowed}

    async def _request(
        self, process, method: str, params: dict[str, Any] | None = None
    ) -> dict:
        self._next_id += 1
        request = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            request["params"] = params
        process.stdin.write((json.dumps(request) + "\n").encode())
        await process.stdin.drain()
        while True:
            raw = await asyncio.wait_for(process.stdout.readline(), self.timeout)
            if not raw:
                raise MCPConnectionError(
                    f"MCP server '{self.server.name}' closed stdout"
                )
            message = json.loads(raw)
            if message.get("id") == self._next_id:
                if "error" in message:
                    raise MCPConnectionError(str(message["error"]))
                return message.get("result", {})

    async def _start(self):
        try:
            process = await asyncio.create_subprocess_exec(
                self.server.command,
                *self.server.args,
                cwd=self.workspace,
                env=self._environment(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError as exc:
            raise MCPConnectionError(str(exc)) from exc
        try:
            await self._request(
                process,
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "aetheris", "version": "0.2.0"},
                },
            )
            process.stdin.write(
                b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
            )
            await process.stdin.drain()
            return process
        except Exception:
            process.terminate()
            await process.wait()
            raise

    async def list_tools(self) -> list[dict[str, Any]]:
        process = await self._start()
        try:
            result = await self._request(process, "tools/list")
            return list(result.get("tools", []))
        finally:
            process.terminate()
            await process.wait()

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        process = await self._start()
        try:
            result = await self._request(
                process, "tools/call", {"name": name, "arguments": arguments}
            )
            chunks = [str(item.get("text", "")) for item in result.get("content", [])]
            text = "\n".join(chunk for chunk in chunks if chunk)
            return f"Error: {text}" if result.get("isError") else text
        finally:
            process.terminate()
            await process.wait()


async def discover_tools(workspace: str) -> list[tuple[MCPServer, dict[str, Any]]]:
    """Discover tools while keeping one broken optional server isolated."""
    discovered = []
    for server in configured_servers(workspace):
        try:
            tools = await MCPClient(server, workspace).list_tools()
        except (MCPConnectionError, OSError, ValueError) as exc:
            # MCP is optional. One unavailable integration must not remove the
            # built-in skills or prevent the parent task from starting.
            logging.getLogger("AetherisAgent").warning(
                "MCP server '%s' skipped: %s", server.name, exc
            )
            continue
        discovered.extend((server, tool) for tool in tools)
    return discovered
