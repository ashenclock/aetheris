from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from nexus.core.agent import Agent
from nexus.core.state import TaskStatus


def response(content=None, tool_calls=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=tool_calls)
            )
        ],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )


@pytest.fixture
def temp_db(tmp_path):
    return str(tmp_path / "agent.sqlite3")


@pytest.mark.asyncio
async def test_agent_initialization_persists_system_prompt(temp_db):
    agent = Agent(model_name="ollama/llama3", db_path=temp_db)
    await agent.init()

    history = await agent.memory.get_history()
    assert len(history) == 1
    assert history[0]["role"] == "system"
    await agent.close()


@pytest.mark.asyncio
async def test_agent_completes_and_persists_usage(temp_db):
    agent = Agent(model_name="mock/offline", db_path=temp_db)
    await agent.init()
    priced_usage = {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "estimated_cost_usd": 0.002,
        "cost_estimate_available": True,
    }

    with (
        patch(
            "nexus.core.agent.acompletion",
            new_callable=AsyncMock,
            return_value=response("Done"),
        ),
        patch("nexus.core.tracker.CostTracker.add_usage", return_value=priced_usage),
    ):
        reply = await agent.chat("Finish the task")

    state = await agent.memory.load_state()
    history = await agent.memory.get_history()
    assert reply == "Done"
    assert state is not None
    assert state.status == TaskStatus.COMPLETED
    assert state.prompt_tokens == 10
    assert state.completion_tokens == 5
    assert state.estimated_cost_usd == 0.002
    assert [item["role"] for item in history] == ["system", "user", "assistant"]
    await agent.close()


@pytest.mark.asyncio
async def test_tool_call_uses_assistant_and_matching_tool_messages(temp_db, tmp_path):
    target = tmp_path / "sample.txt"
    target.write_text("The answer is 42.", encoding="utf-8")
    call = SimpleNamespace(
        id="read-1",
        function=SimpleNamespace(
            name="read_file",
            arguments=f'{{"filepath":"{target}"}}',
        ),
    )
    agent = Agent(model_name="mock/offline", db_path=temp_db)
    await agent.init()
    scripted = AsyncMock(
        side_effect=[response(tool_calls=[call]), response("Found it")]
    )

    with patch("nexus.core.agent.acompletion", scripted):
        reply = await agent.chat("Read the sample file")

    history = await agent.memory.get_history()
    assert reply == "Found it"
    assert history[2]["role"] == "assistant"
    assert history[2]["tool_calls"][0]["id"] == "read-1"
    assert history[3]["role"] == "tool"
    assert history[3]["tool_call_id"] == "read-1"
    assert "The answer is 42." in history[3]["content"]
    await agent.close()


@pytest.mark.asyncio
async def test_failed_model_request_can_resume_same_task(temp_db):
    first = Agent(model_name="mock/offline", db_path=temp_db, session_id="resume-me")
    await first.init()
    with patch(
        "nexus.core.agent.acompletion",
        new_callable=AsyncMock,
        side_effect=TimeoutError("temporary"),
    ):
        reply = await first.chat("Inspect the repository")
    paused = await first.memory.load_state()
    assert "paused" in reply.lower()
    assert paused is not None and paused.status == TaskStatus.PAUSED
    await first.close()

    second = Agent(model_name="mock/offline", db_path=temp_db, session_id="resume-me")
    await second.init()
    usage = {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "estimated_cost_usd": 0.001,
        "cost_estimate_available": True,
    }
    with (
        patch(
            "nexus.core.agent.acompletion",
            new_callable=AsyncMock,
            return_value=response("Recovered"),
        ),
        patch("nexus.core.tracker.CostTracker.add_usage", return_value=usage),
    ):
        answer = await second.chat("Continue")

    resumed = await second.memory.load_state()
    assert answer == "Recovered"
    assert resumed is not None
    assert resumed.goal == "Inspect the repository"
    assert resumed.status == TaskStatus.COMPLETED
    assert resumed.prompt_tokens == 10
    await second.close()


def test_bounded_history_keeps_complete_tool_protocol():
    call_a = {"id": "call-a", "function": {"name": "read_file", "arguments": "{}"}}
    call_b = {"id": "call-b", "function": {"name": "read_file", "arguments": "{}"}}
    history = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "old request"},
        {"role": "assistant", "content": None, "tool_calls": [call_a]},
        {"role": "tool", "content": "old result", "tool_call_id": "call-a"},
        {"role": "user", "content": "recent request"},
        {"role": "assistant", "content": None, "tool_calls": [call_b]},
        {"role": "tool", "content": "recent result", "tool_call_id": "call-b"},
    ]

    bounded = Agent._bounded_history(history, max_messages=4, max_chars=2_000)
    assistant_calls = {
        call["id"] for message in bounded for call in message.get("tool_calls", [])
    }
    tool_results = {
        message["tool_call_id"] for message in bounded if message.get("role") == "tool"
    }
    assert assistant_calls == tool_results == {"call-b"}
    assert bounded[0]["role"] == "system"


def test_bounded_history_caps_text_size():
    history = [
        {"role": "system", "content": "s" * 10_000},
        {"role": "user", "content": "u" * 20_000},
        {"role": "assistant", "content": "a" * 20_000},
    ]
    bounded = Agent._bounded_history(history, max_messages=10, max_chars=1_000)
    assert sum(len(message.get("content") or "") for message in bounded) <= 1_000


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", ["steps", "cost"])
async def test_runtime_pauses_before_another_model_call_when_budget_is_reached(
    temp_db, tmp_path, limit
):
    target = tmp_path / "sample.txt"
    target.write_text("ready", encoding="utf-8")
    call = SimpleNamespace(
        id="read-1",
        function=SimpleNamespace(
            name="read_file", arguments=f'{{"filepath":"{target}"}}'
        ),
    )
    agent = Agent(
        model_name="mock/offline",
        db_path=temp_db,
        max_steps=1 if limit == "steps" else 10,
        cost_budget_usd=0.10 if limit == "cost" else 0.50,
    )
    await agent.init()
    usage = {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "estimated_cost_usd": 0.10 if limit == "cost" else None,
        "cost_estimate_available": limit == "cost",
    }
    model = AsyncMock(return_value=response(tool_calls=[call]))

    with (
        patch("nexus.core.agent.acompletion", model),
        patch("nexus.core.tracker.CostTracker.add_usage", return_value=usage),
    ):
        reply = await agent.chat("Read sample.txt")

    state = await agent.memory.load_state()
    assert "paused" in reply.lower()
    assert model.call_count == 1
    assert state is not None and state.status == TaskStatus.PAUSED
    assert state.human_review_pauses == 1
    if limit == "cost":
        history = await agent.memory.get_history()
        result = next(message for message in history if message.get("role") == "tool")
        assert "not executed" in result["content"]
        assert state.tool_calls == 0
    else:
        assert state.tool_calls == 1
    await agent.close()
