import pytest

from nexus.core.state import TaskState


def test_state_records_success_and_failure_transitions():
    state = TaskState(session_id="task", goal="Fix a test")
    state.record_step("read_file", success=True)
    state.record_step("run_command", success=False, error="exit code 1")

    assert state.step_count == 2
    assert state.tool_calls == 2
    assert state.tool_failures == 1
    assert state.consecutive_failures == 1
    assert state.last_action == "run_command"
    assert state.last_error == "exit code 1"

    state.record_step("run_command", success=True)
    assert state.consecutive_failures == 0
    assert state.last_error is None


def test_step_and_estimated_cost_budgets_pause_in_python():
    state = TaskState(
        session_id="task", goal="bounded", max_steps=2, cost_budget_usd=0.10
    )
    assert not state.should_pause()
    state.estimated_cost_usd = 0.10
    state.cost_estimate_available = True
    assert state.should_pause()

    state.estimated_cost_usd = None
    state.cost_estimate_available = False
    assert not state.should_pause()
    state.record_step("tool", success=True)
    state.record_step("tool", success=True)
    assert state.should_pause()


def test_state_rejects_unbounded_configuration():
    with pytest.raises(ValueError):
        TaskState(session_id="task", goal="bad", max_steps=0)
    with pytest.raises(ValueError):
        TaskState(session_id="task", goal="bad", cost_budget_usd=0)


def test_state_records_child_usage_separately_from_parent_usage():
    state = TaskState(session_id="task", goal="delegate")
    state.record_subagent(
        {
            "prompt_tokens": 12,
            "completion_tokens": 4,
            "estimated_cost_usd": 0.03,
            "cost_estimate_available": True,
            "failed": False,
        }
    )

    assert state.subagent_sessions == 1
    assert state.subagent_prompt_tokens == 12
    assert state.subagent_completion_tokens == 4
    assert state.subagent_estimated_cost_usd == 0.03
