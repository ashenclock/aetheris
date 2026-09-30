import pytest

from evals.run import run_suite


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
