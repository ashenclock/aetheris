import pytest
import aiosqlite
import os
from nexus.core.memory import SessionMemory

@pytest.fixture
def temp_db():
    db_path = "test_memory.db"
    yield db_path
    if os.path.exists(db_path):
        os.remove(db_path)

@pytest.mark.asyncio
async def test_memory_init(temp_db):
    memory = SessionMemory(db_path=temp_db)
    await memory.init_db()
    
    # Assert DB is created
    assert os.path.exists(temp_db)

@pytest.mark.asyncio
async def test_add_and_get_message(temp_db):
    memory = SessionMemory(db_path=temp_db)
    await memory.init_db()
    
    await memory.add_message("user", "Hello Aetheris!")
    await memory.add_message("assistant", "Greetings, human.")
    
    history = await memory.get_history()
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "Hello Aetheris!"
    assert history[1]["role"] == "assistant"
    assert history[1]["content"] == "Greetings, human."

@pytest.mark.asyncio
async def test_compaction_threshold_trigger(temp_db, mocker):
    """
    Test that compaction is triggered when the token threshold is exceeded.
    We mock the _compact_memory method to ensure it's called without hitting LLM APIs.
    """
    memory = SessionMemory(db_path=temp_db)
    memory.token_threshold = 10 # very low threshold for testing
    await memory.init_db()
    
    mock_compact = mocker.patch.object(memory, '_compact_memory', new_callable=mocker.AsyncMock)
    
    # This should trigger compaction because 10 words * 1.3 > 10 tokens
    long_msg = "This is a very long message designed to trigger the compaction logic."
    await memory.add_message("user", long_msg)
    
    mock_compact.assert_called_once()
