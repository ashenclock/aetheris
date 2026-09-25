from __future__ import annotations

import importlib
import inspect
import json
import logging
import os
import pkgutil
from typing import Any

from litellm import acompletion

from nexus.skills.base_skill import BaseSkill

from .memory import SessionMemory
from .policy import DecisionPolicy, build_policy
from .state import TaskState, TaskStatus
from .tracker import CostTracker

logger = logging.getLogger("AetherisAgent")


class Agent:
    def __init__(
        self,
        model_name: str,
        db_path: str = "memory.db",
        session_id: str = "default",
        max_steps: int = 40,
        cost_budget_usd: float = 1.0,
        policy: DecisionPolicy | None = None,
    ):
        self.model_name = model_name
        self.session_id = session_id
        self.max_steps = max_steps
        self.cost_budget_usd = cost_budget_usd
        self.memory = SessionMemory(db_path=db_path, session_id=session_id)
        self.tracker = CostTracker()
        self.policy = policy or build_policy()
        self.skills = self._load_skills()
        self.system_prompt = self._load_system_prompt()

    def _load_system_prompt(self) -> str:
        path = os.path.join(os.path.dirname(__file__), "prompts", "mega_prompt.md")
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return handle.read()
        except FileNotFoundError:
            return "You are Aetheris, a careful software engineering agent."

    def _load_skills(self) -> dict[str, BaseSkill]:
        import nexus.skills

        skills: dict[str, BaseSkill] = {}
        package_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "skills"))
        for _, module_name, _ in pkgutil.iter_modules([package_path]):
            if module_name == "base_skill":
                continue
            try:
                module = importlib.import_module(f"nexus.skills.{module_name}")
                for _, obj in inspect.getmembers(module, inspect.isclass):
                    if issubclass(obj, BaseSkill) and obj is not BaseSkill:
                        skill = obj()
                        skills[skill.name] = skill
            except Exception as exc:
                logger.warning("Could not load skill %s: %s", module_name, exc)
        return skills

    def _tool_schemas(self) -> list[dict[str, Any]] | None:
        if not self.skills:
            return None
        return [
            {"type": "function", "function": skill.get_function_schema()}
            for skill in self.skills.values()
        ]

    async def init(self) -> None:
        await self.memory.init_db()
        if not await self.memory.get_history():
            await self.memory.add_message("system", self.system_prompt)

    async def close(self) -> None:
        await self.policy.close()

    async def chat(self, user_input: str) -> str:
        await self.memory.add_message("user", user_input)
        state = await self._ensure_state(user_input)

        while True:
            if state.should_pause(self.tracker.total_cost):
                state.status = TaskStatus.PAUSED
                await self.memory.checkpoint(state, "Execution budget reached.")
                return self._pause_message(state, "Execution budget reached.")

            messages = await self.memory.get_history()
            messages.append({"role": "system", "content": self._runtime_context(state)})

            try:
                response = await acompletion(
                    model=self.model_name,
                    messages=messages,
                    tools=self._tool_schemas(),
                )
            except Exception as exc:
                state.status = TaskStatus.FAILED
                state.last_error = str(exc)
                await self.memory.checkpoint(state, "LLM request failed.")
                logger.exception("LLM request failed")
                return f"Agent request failed: {exc}"

            self.tracker.add_usage(response)
            message = response.choices[0].message
            tool_calls = self._serialize_tool_calls(getattr(message, "tool_calls", None))

            if not tool_calls:
                reply = message.content or ""
                await self.memory.add_message("assistant", reply)
                state.status = TaskStatus.COMPLETED
                await self.memory.checkpoint(state, "Task completed.")
                return reply

            await self.memory.add_message(
                "assistant",
                message.content,
                tool_calls=tool_calls,
            )

            for tool_call in tool_calls:
                await self._execute_tool_call(tool_call, state)
                await self.memory.checkpoint(state, f"Completed step {state.step_count}.")

                if state.consecutive_failures > 0 or state.step_count % 5 == 0:
                    decision = await self.policy.decide(state)
                    if decision.action == "pause":
                        state.status = TaskStatus.PAUSED
                        await self.memory.checkpoint(state, decision.reason)
                        return self._pause_message(state, decision.reason)

    async def _ensure_state(self, goal: str) -> TaskState:
        state = await self.memory.load_state()
        if state and state.status in {TaskStatus.RUNNING, TaskStatus.PAUSED}:
            state.status = TaskStatus.RUNNING
            await self.memory.save_state(state)
            return state

        state = TaskState(
            session_id=self.session_id,
            goal=goal,
            max_steps=self.max_steps,
            cost_budget_usd=self.cost_budget_usd,
        )
        await self.memory.checkpoint(state, "Task started.")
        return state

    async def _execute_tool_call(self, tool_call: dict[str, Any], state: TaskState) -> None:
        call_id = tool_call["id"]
        function = tool_call["function"]
        name = function["name"]

        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError as exc:
            result = f"Invalid tool arguments: {exc}"
            state.record_step(name, success=False, error=result)
            await self.memory.add_message(
                "tool", result, name=name, tool_call_id=call_id
            )
            return

        skill = self.skills.get(name)
        if skill is None:
            result = f"Unknown tool: {name}"
            state.record_step(name, success=False, error=result)
        else:
            try:
                if skill.requires_confirmation and not await skill.confirm(arguments):
                    result = "Tool execution was not approved."
                    state.record_step(name, success=False, error=result)
                else:
                    result = await skill.execute(**arguments)
                    success = not result.lower().startswith(("error", "failed"))
                    state.record_step(name, success=success, error=None if success else result)
            except Exception as exc:
                result = f"Tool execution failed: {exc}"
                state.record_step(name, success=False, error=result)

        await self.memory.add_message(
            "tool",
            result,
            name=name,
            tool_call_id=call_id,
        )

    @staticmethod
    def _runtime_context(state: TaskState) -> str:
        return (
            "Runtime state (authoritative, compact, and not part of durable chat history):\n"
            f"- goal: {state.goal}\n"
            f"- step: {state.step_count}/{state.max_steps}\n"
            f"- cost budget: ${state.cost_budget_usd:.2f}\n"
            f"- consecutive failures: {state.consecutive_failures}\n"
            f"- last action: {state.last_action or 'none'}\n"
            f"- last error: {state.last_error or 'none'}\n"
            "Continue toward the original goal. Prefer small, reversible actions."
        )

    @staticmethod
    def _serialize_tool_calls(tool_calls: Any) -> list[dict[str, Any]]:
        serialized: list[dict[str, Any]] = []
        for call in tool_calls or []:
            function = getattr(call, "function", None)
            serialized.append(
                {
                    "id": getattr(call, "id", "tool-call"),
                    "type": "function",
                    "function": {
                        "name": getattr(function, "name", ""),
                        "arguments": getattr(function, "arguments", "{}"),
                    },
                }
            )
        return serialized

    @staticmethod
    def _pause_message(state: TaskState, reason: str) -> str:
        return (
            f"Task paused after {state.step_count} steps. {reason} "
            "Resume the same session when you are ready to continue."
        )
