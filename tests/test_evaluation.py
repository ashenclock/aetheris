import pytest

from evals.run import load_tasks, run_suite, run_task


@pytest.mark.asyncio
async def test_offline_evaluation_covers_recovery_and_approval():
    summary = await run_suite()
    assert summary["success_rate"] == 1.0
    assert summary["successful_recoveries"] == 2
    assert summary["human_review_pauses"] == 1
    assert summary["estimated_cost_usd"] is None
    assert all(all(task["checks"].values()) for task in summary["tasks"])
    edit = next(task for task in summary["tasks"] if task["id"] == "edit-and-test")
    assert edit["artifact_checks"] == {"test_demo.py": True}
    assert edit["tool_output_checks"] == {"Ran 1 test": True, "OK": True}


@pytest.mark.asyncio
async def test_offline_evaluation_can_run_a_focused_subset():
    summary = await run_suite({"resume-interrupted-call"})
    assert [task["id"] for task in summary["tasks"]] == ["resume-interrupted-call"]
    assert summary["success_rate"] == 1.0


@pytest.mark.asyncio
async def test_evaluator_rejects_final_answer_without_required_artifact():
    result = await run_task(
        {
            "id": "no-op-final",
            "goal": "Create a required file.",
            "actions": [],
            "final": "Done.",
            "expected_status": "completed",
            "expected_files": {"required.txt": "expected content"},
        }
    )

    assert result["status"] == "completed"
    assert result["status_label"] == "model finished (goal unverified)"
    assert result["checks"]["status"]
    assert not result["checks"]["expected_files"]
    assert not result["success"]


def test_each_offline_task_declares_expected_failure_counters():
    required = {
        "expected_tool_failures",
        "expected_recovered_interrupted_calls",
        "expected_human_review_pauses",
    }
    assert all(required <= task.keys() for task in load_tasks())


@pytest.mark.asyncio
async def test_evaluator_fails_on_unexpected_tool_failure_count():
    result = await run_task(
        {
            "id": "unexpected-retry-failure",
            "goal": "Retry a transient failure.",
            "actions": [
                {"tool": "flaky_once", "arguments": {"note": "first"}},
                {"tool": "flaky_once", "arguments": {"note": "retry"}},
            ],
            "final": "Done.",
            "expected_status": "completed",
            "expected_tool_failures": 0,
            "expected_recovered_interrupted_calls": 0,
            "expected_human_review_pauses": 0,
        }
    )

    assert result["tool_failures"] == 1
    assert not result["checks"]["tool_failures"]
    assert not result["success"]
