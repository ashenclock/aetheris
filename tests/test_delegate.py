from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from nexus.skills.delegate_task import DelegateTaskSkill


def response(text: str):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=text, tool_calls=None)
            )
        ],
        usage=SimpleNamespace(prompt_tokens=4, completion_tokens=3),
    )


@pytest.mark.asyncio
async def test_delegate_task_runs_one_read_only_child(tmp_path):
    skill = DelegateTaskSkill(
        workspace_root=tmp_path,
        model_name="mock/offline",
        db_path=str(tmp_path / "agent.sqlite3"),
        parent_session="parent",
    )
    model = AsyncMock(return_value=response("The child inspected README.md."))
    with patch("nexus.core.agent.acompletion", model):
        report = await skill.execute(task="List the repository evidence.")

    assert "parent:subagent:1" in report
    assert "README.md" in report
    assert model.call_count == 1
    tool_names = {
        item["function"]["name"]
        for item in model.call_args.kwargs["tools"]
    }
    assert "delegate_task" not in tool_names
    assert "read_file" in tool_names
