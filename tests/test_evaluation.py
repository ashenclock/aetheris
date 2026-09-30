import pytest

from evals.run import run_suite


@pytest.mark.asyncio
async def test_offline_evaluation_covers_recovery_and_approval():
    summary = await run_suite()
    assert summary["success_rate"] == 1.0
    assert summary["successful_recoveries"] == 2
    assert summary["human_review_pauses"] == 1
    assert summary["estimated_cost_usd"] is None


@pytest.mark.asyncio
async def test_offline_evaluation_can_run_a_focused_subset():
    summary = await run_suite({"resume-interrupted-call"})
    assert [task["id"] for task in summary["tasks"]] == ["resume-interrupted-call"]
    assert summary["success_rate"] == 1.0
