from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from nexus.core.agent import Agent
from nexus.core.state import TaskState, TaskStatus


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
async def test_responses_adapter_normalizes_function_call(monkeypatch, tmp_path):
    class FakeResponses:
        async def create(self, **kwargs):
            assert kwargs["model"] == "codex-mini-latest"
            assert kwargs["tools"][0]["type"] == "function"
            return SimpleNamespace(
                output=[
                    SimpleNamespace(
                        type="function_call",
                        call_id="codex-call-1",
                        name="read_file",
                        arguments='{"filepath":"README.md"}',
                    )
                ],
                output_text="",
                usage=SimpleNamespace(input_tokens=7, output_tokens=3),
            )

    class FakeClient:
        def __init__(self):
            self.responses = FakeResponses()

        async def close(self):
            return None

    monkeypatch.setitem(
        __import__("sys").modules,
        "openai",
        SimpleNamespace(AsyncOpenAI=FakeClient),
    )
    agent = Agent(
        model_name="responses/codex-mini-latest",
        workspace_root=tmp_path,
        enabled_skill_names={"read_file"},
    )
    result = await agent._request_model(
        [
            {"role": "system", "content": "instructions"},
            {"role": "user", "content": "inspect"},
        ]
    )
    assert result.choices[0].message.tool_calls[0].id == "codex-call-1"
    assert result.usage.prompt_tokens == 7


def test_agent_can_expose_a_bounded_read_only_tool_set(tmp_path):
    agent = Agent(
        model_name="mock/offline",
        workspace_root=tmp_path,
        enabled_skill_names={"read_file", "search_code"},
    )
    assert set(agent.skills) == {"read_file", "search_code"}
    assert all(
        skill.workspace_root == tmp_path.resolve() for skill in agent.skills.values()
    )


def test_codex_model_selects_responses_api_adapter(tmp_path):
    agent = Agent(
        model_name="responses/codex-mini-latest",
        workspace_root=tmp_path,
        enabled_skill_names={"read_file"},
    )
    assert agent._uses_responses_api()
    tools = agent._responses_tools()
    assert tools[0]["type"] == "function"
    assert "function" not in tools[0]


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


@pytest.mark.asyncio
async def test_resume_can_extend_step_limit_without_reducing_it(temp_db, tmp_path):
    target = tmp_path / "fixture.txt"
    target.write_text("safe", encoding="utf-8")
    call = SimpleNamespace(
        id="limit-call",
        function=SimpleNamespace(
            name="read_file", arguments=f'{{"filepath":"{target}"}}'
        ),
    )
    first = Agent(
        model_name="mock/offline",
        db_path=temp_db,
        session_id="extend-limit",
        max_steps=1,
    )
    await first.init()
    with patch(
        "nexus.core.agent.acompletion",
        new_callable=AsyncMock,
        return_value=response(tool_calls=[call]),
    ):
        reply = await first.chat("Read the fixture")
    paused = await first.memory.load_state()
    assert "paused" in reply.lower()
    assert paused is not None and paused.max_steps == 1
    await first.close()

    second = Agent(
        model_name="mock/offline",
        db_path=temp_db,
        session_id="extend-limit",
        max_steps=3,
    )
    await second.init()
    with patch(
        "nexus.core.agent.acompletion",
        new_callable=AsyncMock,
        return_value=response("Recovered"),
    ):
        assert await second.chat("Continue") == "Recovered"
    resumed = await second.memory.load_state()
    assert resumed is not None
    assert resumed.max_steps == 3
    await second.close()


@pytest.mark.asyncio
async def test_malformed_model_response_pauses_with_recoverable_state(temp_db):
    agent = Agent(model_name="mock/offline", db_path=temp_db, session_id="bad-response")
    await agent.init()

    malformed = SimpleNamespace(choices=[])
    with patch(
        "nexus.core.agent.acompletion",
        new_callable=AsyncMock,
        return_value=malformed,
    ):
        reply = await agent.chat("Inspect the repository")

    state = await agent.memory.load_state()
    assert "malformed model response" in reply.lower()
    assert state is not None
    assert state.status == TaskStatus.PAUSED
    assert state.last_action == "model_response"
    assert state.step_count == 1
    await agent.close()


@pytest.mark.asyncio
async def test_empty_model_response_pauses_instead_of_claiming_completion(temp_db):
    agent = Agent(
        model_name="mock/offline", db_path=temp_db, session_id="empty-response"
    )
    await agent.init()

    with patch(
        "nexus.core.agent.acompletion",
        new_callable=AsyncMock,
        return_value=response(""),
    ):
        reply = await agent.chat("Inspect the repository")

    state = await agent.memory.load_state()
    assert "empty model response" in reply.lower()
    assert state is not None
    assert state.status == TaskStatus.PAUSED
    assert state.last_action == "model_response"
    await agent.close()


@pytest.mark.asyncio
async def test_resume_restores_child_session_numbering(temp_db):
    first = Agent(model_name="mock/offline", db_path=temp_db, session_id="parent")
    await first.init()
    await first.memory.checkpoint(
        TaskState(
            session_id="parent",
            goal="Review a repository",
            subagent_children_started=2,
        ),
        "Persisted after two child sessions.",
    )
    await first.close()

    resumed = Agent(model_name="mock/offline", db_path=temp_db, session_id="parent")
    await resumed.init()
    assert resumed.skills["delegate_task"]._delegation_count == 2
    await resumed.close()


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
