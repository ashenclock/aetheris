from __future__ import annotations

import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from nexus.api import AetherisAPIServer


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
