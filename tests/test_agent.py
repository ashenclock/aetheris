from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from nexus.core.agent import Agent
from nexus.core.state import TaskStatus


@pytest.fixture
def temp_db(tmp_path):
    return str(tmp_path / "agent.db")


@pytest.fixture
def mock_litellm():
    with patch("nexus.core.agent.acompletion", new_callable=AsyncMock) as mocked:
        message = SimpleNamespace(content="Mocked LLM response", tool_calls=None)
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
        mocked.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            usage=usage,
            _hidden_params={},
        )
        yield mocked


@pytest.mark.asyncio
async def test_agent_initialization(temp_db):
    agent = Agent(model_name="ollama/llama3", db_path=temp_db)
    await agent.init()

    history = await agent.memory.get_history()
    assert len(history) == 1
    assert history[0]["role"] == "system"

    await agent.close()


@pytest.mark.asyncio
async def test_agent_chat_persists_completed_state(mock_litellm, temp_db):
    agent = Agent(model_name="ollama/llama3", db_path=temp_db)
    await agent.init()

    with patch("nexus.core.tracker.CostTracker.add_usage"):
        reply = await agent.chat("Are you functional?")

    assert reply == "Mocked LLM response"
    mock_litellm.assert_called_once()

    history = await agent.memory.get_history()
    assert [item["role"] for item in history] == ["system", "user", "assistant"]

    state = await agent.memory.load_state()
    assert state is not None
    assert state.status == TaskStatus.COMPLETED

    checkpoint = await agent.memory.latest_checkpoint()
    assert checkpoint is not None
    assert checkpoint[1] == "Task completed."

    await agent.close()
