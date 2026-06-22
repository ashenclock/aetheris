import pytest
import os
from unittest.mock import patch, AsyncMock
from nexus.core.agent import Agent

@pytest.fixture
def temp_db():
    db_path = "test_agent.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    yield db_path
    if os.path.exists(db_path):
        os.remove(db_path)

@pytest.fixture
def mock_litellm():
    with patch("nexus.core.agent.acompletion", new_callable=AsyncMock) as mock_acompletion:
        # Mock the litellm response structure
        mock_response = AsyncMock()
        mock_response.choices = [AsyncMock()]
        mock_response.choices[0].message.content = "Mocked LLM Response"
        
        # Mock usage tracking
        mock_response.usage = AsyncMock()
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        
        mock_acompletion.return_value = mock_response
        yield mock_acompletion

@pytest.mark.asyncio
async def test_agent_initialization(temp_db):
    agent = Agent(model_name="ollama/llama3", db_path=temp_db)
    await agent.init()
    
    # Assert system prompt is added
    history = await agent.memory.get_history()
    assert len(history) == 1
    assert history[0]["role"] == "system"

@pytest.mark.asyncio
async def test_agent_chat_and_cost_tracking(mock_litellm, temp_db):
    agent = Agent(model_name="ollama/llama3", db_path=temp_db)
    await agent.init()
    
    # Send a message
    reply = await agent.chat("Are you functional?")
    
    # Assert acompletion was called
    mock_litellm.assert_called_once()
    
    # Assert reply matches mocked response
    assert reply == "Mocked LLM Response"
    
    # Assert memory is updated
    history = await agent.memory.get_history()
    assert len(history) == 3 # system, user, assistant
    assert history[1]["role"] == "user"
    assert history[1]["content"] == "Are you functional?"
    assert history[2]["role"] == "assistant"
    assert history[2]["content"] == "Mocked LLM Response"
    
    # Assert tracking is updated
    summary = agent.tracker.summary()
    assert summary["prompt_tokens"] == 10
    assert summary["completion_tokens"] == 5
