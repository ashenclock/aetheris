from __future__ import annotations

import asyncio
import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .core.agent import Agent
from .core.memory import SessionMemory


class AetherisAPIError(RuntimeError):
    """Raised for invalid API requests."""


class AetherisRequestHandler(BaseHTTPRequestHandler):
    server_version = "Aetheris/0.2"

    def log_message(self, format: str, *args: Any) -> None:
        print(f"aetheris-api: {format % args}")

    @property
    def runtime(self) -> "AetherisAPIServer":
        return self.server  # type: ignore[return-value]

    def _authorized(self) -> bool:
        expected = self.runtime.api_token
        if not expected:
            return True
        supplied = self.headers.get("Authorization", "")
        return supplied == f"Bearer {expected}"

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > 64 * 1024:
                raise AetherisAPIError(
                    "Request body must be between 1 byte and 64 KiB."
                )
            value = json.loads(self.rfile.read(size))
        except (ValueError, json.JSONDecodeError) as exc:
            raise AetherisAPIError("Request body must be valid JSON.") from exc
        if not isinstance(value, dict):
            raise AetherisAPIError("Request body must be a JSON object.")
        return value

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/healthz":
            self._write_json(HTTPStatus.OK, {"status": "ok", "service": "aetheris"})
            return
        if not self._authorized():
            self._write_json(HTTPStatus.UNAUTHORIZED, {"error": "invalid bearer token"})
            return
        prefix = "/v1/tasks/"
        if path.startswith(prefix):
            session = unquote(path.removeprefix(prefix)).strip()
            self._write_json(HTTPStatus.OK, _run(self.runtime.task_status(session)))
            return
        self._write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/v1/tasks":
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if not self._authorized():
            self._write_json(HTTPStatus.UNAUTHORIZED, {"error": "invalid bearer token"})
            return
        try:
            body = self._read_json()
            result = _run(self.runtime.run_task(body))
        except AetherisAPIError as exc:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
        else:
            self._write_json(HTTPStatus.OK, result)


class AetherisAPIServer(ThreadingHTTPServer):
    """Small n8n-friendly adapter around the existing Agent runtime."""

    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        *,
        db_path: str,
        workspace: str,
        model: str,
        max_steps: int,
        cost_budget: float,
        read_only: bool,
        api_token: str | None,
    ) -> None:
        super().__init__(address, AetherisRequestHandler)
        self.db_path = db_path
        self.workspace = Path(workspace).expanduser().resolve()
        self.model = model
        self.max_steps = max_steps
        self.cost_budget = cost_budget
        self.read_only = read_only
        self.api_token = api_token
        self._task_lock = threading.Lock()

    async def run_task(self, body: dict[str, Any]) -> dict[str, Any]:
        goal = body.get("goal")
        session = body.get("session", "n8n")
        if not isinstance(goal, str) or not goal.strip():
            raise AetherisAPIError("'goal' must be a non-empty string.")
        if not isinstance(session, str) or not session.strip() or len(session) > 100:
            raise AetherisAPIError("'session' must be a short non-empty string.")
        max_steps = body.get("max_steps", self.max_steps)
        if not isinstance(max_steps, int) or not 1 <= max_steps <= self.max_steps:
            raise AetherisAPIError(
                f"'max_steps' must be an integer between 1 and {self.max_steps}."
            )

        enabled = (
            {"list_directory", "read_file", "search_code", "recall"}
            if self.read_only
            else None
        )
        agent = Agent(
            model_name=self.model,
            db_path=self.db_path,
            session_id=session,
            max_steps=max_steps,
            cost_budget_usd=self.cost_budget,
            workspace_root=self.workspace,
            enabled_skill_names=enabled,
        )
        with self._task_lock:
            await agent.init()
            try:
                reply = await agent.chat(goal)
                state = await agent.memory.load_state()
                return {
                    "session": session,
                    "reply": reply,
                    "state": state.model_dump(mode="json") if state else None,
                }
            finally:
                await agent.close()

    async def task_status(self, session: str) -> dict[str, Any]:
        if not session:
            raise AetherisAPIError("session cannot be empty")
        memory = SessionMemory(db_path=self.db_path, session_id=session)
        await memory.init_db()
        state = await memory.load_state()
        return {
            "session": session,
            "state": state.model_dump(mode="json") if state else None,
        }


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
    db_path: str = "aetheris_memory.db",
    workspace: str = ".",
    model: str = "ollama/llama3",
    max_steps: int = 12,
    cost_budget: float = 0.25,
    read_only: bool = True,
    api_token: str | None = None,
) -> None:
    server = AetherisAPIServer(
        (host, port),
        db_path=db_path,
        workspace=workspace,
        model=model,
        max_steps=max_steps,
        cost_budget=cost_budget,
        read_only=read_only,
        api_token=api_token,
    )
    print(f"Aetheris API listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping Aetheris API.")
    finally:
        server.server_close()


def _run(coroutine):
    return asyncio.run(coroutine)
