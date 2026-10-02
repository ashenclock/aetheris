import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nexus.chat import ChatControls
from nexus.web.session import run_turn


def response(content=None, call_id=None, filename=None):
    calls = (
        None
        if call_id is None
        else [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": "write_file",
                    "arguments": json.dumps(
                        {"filepath": filename, "content": "fixture"}
                    ),
                },
            }
        ]
    )
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=content,
                    tool_calls=[
                        SimpleNamespace(
                            id=call["id"], function=SimpleNamespace(**call["function"])
                        )
                        for call in calls
                    ]
                    if calls
                    else None,
                )
            )
        ],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )


@pytest.mark.asyncio
async def test_web_approval_is_durable_and_only_authorizes_one_call(
    tmp_path, monkeypatch
):
    controls = ChatControls(tmp_path, str(tmp_path / "state.db"), "mock/offline")
    model = AsyncMock(
        side_effect=[
            response(call_id="first", filename="first.txt"),
            response(call_id="second", filename="second.txt"),
        ]
    )
    monkeypatch.setattr("nexus.core.agent.acompletion", model)
    reply, state, _ = await run_turn(controls, "Create two files")
    assert state["pending_approval"]["id"] == "first"
    assert not (tmp_path / "first.txt").exists()
    assert "approval" in reply.lower()
    # A stale/wrong decision cannot execute a pending action.
    _, stale_state, _ = await run_turn(
        controls, "Continue", approval_id="wrong-id", approval=True
    )
    assert stale_state["pending_approval"]["id"] == "first"
    assert not (tmp_path / "first.txt").exists()
    # Recreate controls, just as a rerun/reopened browser would.
    reopened = ChatControls(tmp_path, controls.database, "mock/offline")
    _, state, history = await run_turn(
        reopened, "Continue", approval_id="first", approval=True
    )
    assert (tmp_path / "first.txt").read_text() == "fixture"
    assert not (tmp_path / "second.txt").exists()
    assert state["pending_approval"]["id"] == "second"
    assert sum(item.get("tool_call_id") == "first" for item in history) == 1
    _, state, _ = await run_turn(
        reopened, "Reject", approval_id="second", approval=False
    )
    assert state["pending_approval"] is None
    assert not (tmp_path / "second.txt").exists()
    assert model.await_count == 2


@pytest.mark.asyncio
async def test_chat_controls_inspect_without_model_calls(tmp_path):
    controls = ChatControls(
        tmp_path, str(tmp_path / "state.db"), "mock/offline", read_only=True
    )
    await controls.memory.init_db()
    assert "/wiki" in (await controls.command("/")).text
    assert (await controls.command("/resume")).data == []
    assert "not found" in (await controls.command("/resume missing")).text
    await controls.command("/permissions session")
    assert not controls.session_approval
    assert (await controls.command("/trace")).data == {
        "messages": [],
        "checkpoints": [],
    }
