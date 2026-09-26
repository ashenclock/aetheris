import pytest

from nexus.core.memory import SessionMemory
from nexus.core.state import TaskState, TaskStatus


@pytest.fixture
def temp_db(tmp_path):
    return str(tmp_path / "memory.db")


@pytest.mark.asyncio
async def test_message_roundtrip_supports_tool_protocol(temp_db):
    memory = SessionMemory(db_path=temp_db, session_id="demo")
    await memory.init_db()

    tool_calls = [
        {
            "id": "call-1",
            "type": "function",
            "function": {"name": "read_file", "arguments": '{"path":"README.md"}'},
        }
    ]
    await memory.add_message("assistant", None, tool_calls=tool_calls)
    await memory.add_message(
        "tool",
        "file contents",
        name="read_file",
        tool_call_id="call-1",
    )

    history = await memory.get_history()
    assert history[0]["tool_calls"] == tool_calls
    assert history[1]["role"] == "tool"
    assert history[1]["tool_call_id"] == "call-1"


@pytest.mark.asyncio
async def test_state_and_checkpoint_roundtrip(temp_db):
    memory = SessionMemory(db_path=temp_db, session_id="demo")
    await memory.init_db()

    state = TaskState(session_id="demo", goal="Fix the tests")
    state.record_step("read_file", success=True)
    await memory.checkpoint(state, "Inspected repository.")

    restored = await memory.load_state()
    latest = await memory.latest_checkpoint()

    assert restored is not None
    assert restored.step_count == 1
    assert restored.checkpoint_count == 1
    assert latest is not None
    assert latest[0].goal == "Fix the tests"
    assert latest[1] == "Inspected repository."


@pytest.mark.asyncio
async def test_interrupted_tool_call_gets_result_and_checkpoint(temp_db):
    memory = SessionMemory(db_path=temp_db, session_id="demo")
    await memory.init_db()
    state = TaskState(session_id="demo", goal="Continue the task")
    await memory.checkpoint(state, "Started")
    await memory.add_message(
        "assistant",
        None,
        tool_calls=[
            {
                "id": "interrupted-1",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": '{"filepath":"file.py"}',
                },
            }
        ],
    )

    recovered = await memory.recover_pending_tool_calls(state)
    history = await memory.get_history()
    restored = await memory.load_state()
    latest = await memory.latest_checkpoint()

    assert recovered == 1
    assert history[-1]["role"] == "tool"
    assert history[-1]["tool_call_id"] == "interrupted-1"
    assert history[-1]["content"].startswith("Error:")
    assert restored is not None
    assert restored.status == TaskStatus.RUNNING
    assert restored.tool_calls == 1
    assert restored.tool_failures == 1
    assert restored.recovered_interrupted_calls == 1
    assert latest is not None
    assert latest[1] == "Recovered interrupted tool calls."
    assert await memory.recover_pending_tool_calls(state) == 0


@pytest.mark.asyncio
async def test_tool_result_and_state_share_one_checkpoint(temp_db):
    memory = SessionMemory(db_path=temp_db, session_id="demo")
    await memory.init_db()
    state = TaskState(session_id="demo", goal="Read a file")
    state.record_step("read_file", success=True)

    await memory.checkpoint(
        state,
        "Recorded tool result.",
        tool_results=[
            {"name": "read_file", "tool_call_id": "call-1", "content": "done"}
        ],
    )

    history = await memory.get_history()
    restored = await memory.load_state()
    latest = await memory.latest_checkpoint()
    assert history[0]["tool_call_id"] == "call-1"
    assert restored is not None and restored.tool_calls == 1
    assert latest is not None and latest[0].tool_calls == 1
