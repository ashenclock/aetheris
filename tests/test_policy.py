from unittest.mock import AsyncMock

import pytest

from nexus.core.policy import HeuristicPolicy, JevPolicy, build_policy
from nexus.core.state import TaskState


@pytest.mark.asyncio
async def test_heuristic_policy_pauses_after_repeated_failures():
    state = TaskState(
        session_id="task",
        goal="recover",
        consecutive_failures=3,
        last_error="repeated tool error",
    )
    decision = await HeuristicPolicy().decide(state)
    assert decision.action == "pause"


def test_missing_jev_dependency_falls_back(monkeypatch):
    monkeypatch.setenv("AETHERIS_DECISION_POLICY", "jev")
    policy = build_policy()
    assert isinstance(policy, HeuristicPolicy)


@pytest.mark.asyncio
async def test_jev_request_error_uses_deterministic_fallback():
    policy = JevPolicy.__new__(JevPolicy)
    policy._client = AsyncMock()
    policy._client.system_one.side_effect = RuntimeError("service unavailable")
    state = TaskState(session_id="task", goal="recover", consecutive_failures=3)

    decision = await policy.decide(state)
    assert decision.action == "pause"
