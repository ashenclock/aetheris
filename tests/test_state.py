import pytest

from nexus.cli import _print_state, _state_payload
from nexus.core.state import TaskState, TaskStatus


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


def test_completed_status_is_a_model_protocol_state_not_goal_verification():
    state = TaskState(session_id="task", goal="Build a working application")
    state.status = TaskStatus.COMPLETED

    assert state.status.value == "completed"
    assert state.status_label == "model finished (goal unverified)"
    assert state.tool_calls == 0

    payload = state.public_payload()
    assert payload["status"] == "completed"
    assert payload["status_label"] == "model finished (goal unverified)"
    assert "status_label" not in state.model_dump(mode="json")
    assert _state_payload(state)["status_label"] == payload["status_label"]


def test_cli_labels_completed_status_as_unverified(capsys):
    state = TaskState(session_id="task", goal="Build a working application")
    state.status = TaskStatus.COMPLETED

    _print_state(state)

    assert "model finished (goal unverified)" in capsys.readouterr().out


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


def test_three_consecutive_failures_are_a_runtime_stop_condition():
    state = TaskState(session_id="task", goal="bounded", consecutive_failures=3)
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
