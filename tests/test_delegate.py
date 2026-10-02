from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from nexus.skills.delegate_task import DelegateTaskSkill
from nexus.core.delegation import DelegationBudget


def response(text: str):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=None))
        ],
        usage=SimpleNamespace(prompt_tokens=4, completion_tokens=3),
    )


@pytest.mark.asyncio
async def test_delegate_task_runs_one_read_only_child(tmp_path):
    skill = DelegateTaskSkill(
        workspace_root=tmp_path,
        model_name="mock/offline",
        subagent_model="mock/local-small",
        db_path=str(tmp_path / "agent.sqlite3"),
        parent_session="parent",
        max_completion_tokens=321,
    )
    model = AsyncMock(return_value=response("The child inspected README.md."))
    with patch("nexus.core.agent.acompletion", model):
        report = await skill.execute(task="List the repository evidence.")

    assert "parent:subagent:1" in report
    assert "README.md" in report
    assert model.call_count == 1
    tool_names = {item["function"]["name"] for item in model.call_args.kwargs["tools"]}
    assert "delegate_task" not in tool_names
    assert "read_file" in tool_names
    assert model.call_args.kwargs["model"] == "mock/local-small"
    assert model.call_args.kwargs["max_tokens"] == 321


def test_delegation_budget_stops_after_configured_children():
    budget = DelegationBudget(total_usd=0.10, max_children=1)
    reservation = budget.reserve(0.10)
    budget.settle(reservation, actual_cost_usd=None, cost_available=False)

    with pytest.raises(RuntimeError, match="maximum child-session count"):
        budget.reserve(0.01)


def test_delegation_budget_restores_after_restart():
    budget = DelegationBudget(total_usd=0.20, max_children=2)
    reservation = budget.reserve(0.10)
    budget.settle(reservation, actual_cost_usd=None, cost_available=False)

    restored = DelegationBudget(total_usd=1.0, max_children=10)
    restored.restore(budget.snapshot())

    assert restored.total_usd == 0.20
    assert restored.spent_usd == 0.10
    assert restored.children_started == 1
