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
        client_options = {}
        if api_key := os.getenv("AETHERIS_DECISION_API_KEY"):
            client_options["api_key"] = api_key
        if base_url := os.getenv("AETHERIS_DECISION_BASE_URL"):
            client_options["base_url"] = base_url
        if model := os.getenv("AETHERIS_DECISION_MODEL"):
            client_options["model"] = model
        self._client = AsyncTypeSafeClient(**client_options)
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
                answer.choice == "continue"
                and answer.confidence >= self.confidence_threshold
            ):
                return PolicyDecision(
                    "continue", "Jev allowed the run to continue.", answer.confidence
                )
            reason = (
                "Jev requested human review."
                if answer.choice == "pause"
                else "Jev was uncertain; pausing rather than failing open."
            )
            return PolicyDecision("pause", reason, answer.confidence)
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
        except Exception as exc:
            logger.warning("Jev is unavailable; using deterministic policy: %s", exc)
    return HeuristicPolicy()
