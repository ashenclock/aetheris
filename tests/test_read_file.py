from unittest.mock import patch

import pytest

from nexus.skills.read_file import ReadFileSkill
from nexus.core.agent import Agent


@pytest.mark.asyncio
async def test_sensitive_files_require_approval(tmp_path):
    secret = tmp_path / ".env"
    secret.write_text("FAKE_KEY=fixture\n", encoding="utf-8")
    skill = ReadFileSkill(tmp_path)

    with patch("rich.prompt.Confirm.ask", return_value=False):
        assert not await skill.confirm({"filepath": ".env"})
    with patch("rich.prompt.Confirm.ask", return_value=True):
        assert await skill.confirm({"filepath": ".env"})


@pytest.mark.asyncio
async def test_normal_files_remain_readable_without_approval(tmp_path):
    normal = tmp_path / "README.md"
    normal.write_text("safe fixture\n", encoding="utf-8")
    skill = ReadFileSkill(tmp_path)

    assert await skill.confirm({"filepath": "README.md"})
    assert await skill.execute(filepath="README.md") == "safe fixture\n"


@pytest.mark.asyncio
async def test_agent_applies_sensitive_read_gate(tmp_path):
    secret = tmp_path / ".env"
    secret.write_text("FAKE_KEY=fixture\n", encoding="utf-8")
    agent = Agent(model_name="mock/offline", workspace_root=tmp_path)
    await agent.init()
    state = await agent._ensure_state("read a secret")
    call = {
        "id": "read-secret",
        "function": {
            "name": "read_file",
            "arguments": '{"filepath": ".env"}',
        },
    }
    with patch.object(agent.skills["read_file"], "confirm", return_value=False):
        await agent._execute_tool_call(call, state)
    history = await agent.memory.get_history()
    assert "not approved" in history[-1]["content"]
    assert "FAKE_KEY" not in history[-1]["content"]
    await agent.close()
