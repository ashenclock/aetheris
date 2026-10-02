from unittest.mock import AsyncMock

import pytest

from nexus.core.agent import Agent


@pytest.mark.asyncio
async def test_session_approval_can_be_revoked_and_is_not_persisted(tmp_path):
    agent = Agent("mock/offline", str(tmp_path / "state.db"), workspace_root=tmp_path)
    await agent.init()
    state = await agent._ensure_state("Write a fixture")
    skill = agent.skills["write_file"]
    skill.confirm = AsyncMock(return_value=False)
    call = {
        "id": "write-1",
        "function": {
            "name": "write_file",
            "arguments": '{"filepath":"allowed.txt","content":"fixture"}',
        },
    }
    agent.session_approval = True
    assert not await agent._execute_tool_call(call, state)
    assert (tmp_path / "allowed.txt").read_text() == "fixture"
    skill.confirm.assert_not_called()

    agent.session_approval = False
    call["id"] = "write-2"
    call["function"]["arguments"] = '{"filepath":"blocked.txt","content":"fixture"}'
    assert await agent._execute_tool_call(call, state)
    assert not (tmp_path / "blocked.txt").exists()
    await agent.close()
    reopened = Agent(
        "mock/offline", str(tmp_path / "state.db"), workspace_root=tmp_path
    )
    assert not reopened.session_approval
    await reopened.close()
