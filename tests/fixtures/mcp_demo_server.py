"""Tiny JSONL MCP fixture used by connection tests and the interview demo."""

import json
import sys


def reply(message: dict) -> None:
    result = {"jsonrpc": "2.0", "id": message.get("id")}
    method = message.get("method")
    if method == "initialize":
        result["result"] = {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "aetheris-fixture", "version": "1"},
        }
    elif method == "tools/list":
        result["result"] = {
            "tools": [
                {
                    "name": "echo",
                    "description": "Return a deterministic test message.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"message": {"type": "string"}},
                        "required": ["message"],
                    },
                }
            ]
        }
    elif method == "tools/call":
        arguments = message.get("params", {}).get("arguments", {})
        result["result"] = {
            "content": [
                {"type": "text", "text": f"echo:{arguments.get('message', '')}"}
            ]
        }
    elif method == "notifications/initialized":
        return
    else:
        result["error"] = {"code": -32601, "message": f"Unknown method: {method}"}
    print(json.dumps(result), flush=True)


for line in sys.stdin:
    try:
        message = json.loads(line)
        if isinstance(message, dict) and "id" in message:
            reply(message)
    except json.JSONDecodeError:
        continue
