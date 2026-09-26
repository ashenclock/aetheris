from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from typing import Protocol

from .state import TaskState

logger = logging.getLogger("AetherisPolicy")


@dataclass(frozen=True)
class PolicyDecision:
    action: str
    reason: str
    confidence: float = 1.0


class DecisionPolicy(Protocol):
    async def decide(self, state: TaskState) -> PolicyDecision: ...
    async def close(self) -> None: ...


class HeuristicPolicy:
    async def decide(self, state: TaskState) -> PolicyDecision:
        if state.consecutive_failures >= 3:
            return PolicyDecision("pause", "Three consecutive tool failures.")
        return PolicyDecision("continue", "No pause condition detected.")

    async def close(self) -> None:
        return None


class JevPolicy:
    """Optional Jev watchdog for narrow continue-vs-pause decisions."""

    def __init__(self, confidence_threshold: float = 0.70):
        try:
            from typesafe_sdk import AsyncTypeSafeClient, Choice
        except ImportError as exc:
            raise RuntimeError(
                "Jev policy requires the optional 'typesafe-sdk' dependency."
            ) from exc

        self._Choice = Choice
        self._client = AsyncTypeSafeClient()
        self.confidence_threshold = confidence_threshold

    async def decide(self, state: TaskState) -> PolicyDecision:
        try:
            response = await self._client.system_one(
                state={
                    "goal": state.goal,
                    "step_count": state.step_count,
                    "max_steps": state.max_steps,
                    "consecutive_failures": state.consecutive_failures,
                    "last_action": state.last_action,
                    "last_error": state.last_error,
                },
                questions={
                    "horizon_gate": self._Choice(
                        instructions=(
                            "Should this autonomous run continue, or pause for human review? "
                            "Pause only when repeated failures or ambiguous recovery make further "
                            "autonomous execution unsafe or wasteful."
                        ),
                        criteria={
                            "continue": "The run is making progress and can safely continue.",
                            "pause": "The run should stop and request human review before proceeding.",
                        },
                    )
                },
            )
            answer = response.choices["horizon_gate"]
            if (
                answer.choice == "pause"
                and answer.confidence >= self.confidence_threshold
            ):
                return PolicyDecision(
                    "pause", "Jev requested human review.", answer.confidence
                )
            return PolicyDecision(
                "continue", "Jev allowed the run to continue.", answer.confidence
            )
        except Exception as exc:
            logger.warning(
                "Jev decision failed; applying deterministic policy: %s", exc
            )
            return await HeuristicPolicy().decide(state)

    async def close(self) -> None:
        await self._client.aclose()


def build_policy() -> DecisionPolicy:
    if os.getenv("AETHERIS_DECISION_POLICY", "heuristic").lower() == "jev":
        try:
            return JevPolicy()
        except RuntimeError as exc:
            logger.warning("Jev is unavailable; using deterministic policy: %s", exc)
    return HeuristicPolicy()
