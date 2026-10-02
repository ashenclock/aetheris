from __future__ import annotations

import json
import socket
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from nexus.api import AetherisAPIError, AetherisAPIServer
from nexus.core.memory import SessionBusyError, SessionMemory
from nexus.core.state import TaskState, TaskStatus


def make_server(tmp_path, *, host="127.0.0.1", api_token=None, read_only=True):
    return AetherisAPIServer(
        (host, 0),
        db_path=str(tmp_path / "state.sqlite3"),
        workspace=str(tmp_path),
        model="mock/offline",
        max_steps=4,
        cost_budget=0.1,
        read_only=read_only,
        api_token=api_token,
    )


def test_api_requires_token_when_binding_to_network(tmp_path):
    with pytest.raises(ValueError, match="AETHERIS_API_TOKEN is required"):
        make_server(tmp_path, host="0.0.0.0")


def test_api_can_bind_loopback_without_token(tmp_path):
    server = make_server(tmp_path)
    server.server_close()


def test_api_refuses_write_mode_without_remote_approval_workflow(tmp_path):
    with pytest.raises(ValueError, match="no remote approval workflow"):
        make_server(tmp_path, read_only=False)


@pytest.mark.asyncio
async def test_api_rejects_boolean_step_budget(tmp_path):
    server = make_server(tmp_path)

    with pytest.raises(AetherisAPIError, match="must be an integer"):
        await server.run_task({"goal": "test", "max_steps": True})

    server.server_close()


@pytest.mark.asyncio
async def test_api_status_read_does_not_create_a_missing_database(tmp_path):
    database = tmp_path / "missing.sqlite3"
    server = AetherisAPIServer(
        ("127.0.0.1", 0),
        db_path=str(database),
        workspace=str(tmp_path),
        model="mock/offline",
        max_steps=4,
        cost_budget=0.1,
        read_only=True,
        api_token=None,
    )

    result = await server.task_status("missing-task")

    assert result == {"session": "missing-task", "state": None}
    assert not database.exists()
    server.server_close()


@pytest.mark.asyncio
async def test_api_status_reads_an_existing_task_from_read_only_database(tmp_path):
    database = tmp_path / "state.sqlite3"
    memory = SessionMemory(str(database), "task-1")
    await memory.init_db()
    state = TaskState(session_id="task-1", goal="inspect")
    state.status = TaskStatus.COMPLETED
    await memory.save_state(state)
    server = AetherisAPIServer(
        ("127.0.0.1", 0),
        db_path=str(database),
        workspace=str(tmp_path),
        model="mock/offline",
        max_steps=4,
        cost_budget=0.1,
        read_only=True,
        api_token=None,
    )

    result = await server.task_status("task-1")

    assert result["state"]["goal"] == "inspect"
    assert result["state"]["status"] == "completed"
    assert result["state"]["status_label"] == "model finished (goal unverified)"
    server.server_close()


def test_api_does_not_return_internal_exception_details(tmp_path):
    server = make_server(tmp_path, api_token="test-token")

    async def fail_with_internal_path(_body):
        raise RuntimeError("private-internal-path")

    server.run_task = fail_with_internal_path
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    request = Request(
        f"http://127.0.0.1:{server.server_port}/v1/tasks",
        data=json.dumps({"goal": "test"}).encode(),
        headers={
            "Authorization": "Bearer test-token",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with pytest.raises(HTTPError) as caught:
            urlopen(request, timeout=3)
        payload = json.loads(caught.value.read())
        assert caught.value.code == 500
        assert payload["error"] == "Internal server error."
        assert payload["request_id"]
        assert "private-internal-path" not in json.dumps(payload)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_api_returns_conflict_for_an_active_session(tmp_path):
    server = make_server(tmp_path, api_token="test-token")

    async def busy_task(_body):
        raise SessionBusyError("Session 'task-1' is already active.")

    server.run_task = busy_task
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    request = Request(
        f"http://127.0.0.1:{server.server_port}/v1/tasks",
        data=json.dumps({"goal": "test", "session_id": "task-1"}).encode(),
        headers={
            "Authorization": "Bearer test-token",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with pytest.raises(HTTPError) as caught:
            urlopen(request, timeout=3)
        payload = json.loads(caught.value.read())
        assert caught.value.code == 409
        assert "already active" in payload["error"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_api_rejects_chunked_request_bodies(tmp_path):
    server = make_server(tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with socket.create_connection(
            ("127.0.0.1", server.server_port), timeout=3
        ) as client:
            client.sendall(
                b"POST /v1/tasks HTTP/1.1\r\nHost: localhost\r\n"
                b"Transfer-Encoding: chunked\r\n\r\n0\r\n\r\n"
            )
            chunks = []
            while chunk := client.recv(4096):
                chunks.append(chunk)
            response = b"".join(chunks)
        assert b"400 Bad Request" in response
        assert b"Transfer-Encoding is not supported" in response
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_api_times_out_on_an_incomplete_request_body(tmp_path, monkeypatch):
    monkeypatch.setattr("nexus.api.REQUEST_READ_TIMEOUT_SECONDS", 0.1)
    server = make_server(tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with socket.create_connection(
            ("127.0.0.1", server.server_port), timeout=3
        ) as client:
            client.sendall(
                b"POST /v1/tasks HTTP/1.1\r\nHost: localhost\r\n"
                b"Content-Length: 100\r\nContent-Type: application/json\r\n\r\n{"
            )
            response = bytearray()
            while chunk := client.recv(4096):
                response.extend(chunk)
        assert b"408 Request Timeout" in response
        assert b"Request body timed out" in response
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_api_does_not_call_provider_timeout_a_body_timeout(tmp_path):
    server = make_server(tmp_path)

    async def provider_timeout(_body):
        raise TimeoutError("provider deadline")

    server.run_task = provider_timeout
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    request = Request(
        f"http://127.0.0.1:{server.server_port}/v1/tasks",
        data=json.dumps({"goal": "test"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with pytest.raises(HTTPError) as caught:
            urlopen(request, timeout=3)
        payload = json.loads(caught.value.read())
        assert caught.value.code == 500
        assert payload["error"] == "Internal server error."
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
