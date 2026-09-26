from __future__ import annotations

import importlib
import inspect
import json
import logging
import os
import pkgutil
from typing import Any

# Use LiteLLM's packaged price map so importing the agent does not fetch pricing over the network.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
from litellm import acompletion

from nexus.skills.base_skill import BaseSkill

from .knowledge import KnowledgeStore
from .memory import SessionMemory
from .policy import DecisionPolicy, HeuristicPolicy, build_policy
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
        self.knowledge = KnowledgeStore()
        self.system_prompt = self._load_system_prompt()

    def _load_system_prompt(self) -> str:
        path = os.path.join(os.path.dirname(__file__), "prompts", "mega_prompt.md")
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return handle.read()
        except FileNotFoundError:
            return "You are Aetheris, a careful software engineering agent."

    def _load_skills(self) -> dict[str, BaseSkill]:

        skills: dict[str, BaseSkill] = {}
        package_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "skills")
        )
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
        state = await self.memory.load_state()
        if state is not None:
            self.tracker.restore(state)

    async def close(self) -> None:
        await self.policy.close()

    async def chat(self, user_input: str) -> str:
        await self.memory.add_message("user", user_input)
        state = await self._ensure_state(user_input)
        await self.memory.recover_pending_tool_calls(state)

        while True:
            if state.should_pause():
                state.status = TaskStatus.PAUSED
                state.human_review_pauses += 1
                await self.memory.checkpoint(state, "Execution budget reached.")
                return self._pause_message(state, "Execution budget reached.")

            history = self._bounded_history(await self.memory.get_history())
            latest_user_message = next(
                (
                    message.get("content", "")
                    for message in reversed(history)
                    if message.get("role") == "user"
                ),
                "",
            )
            runtime_context = self._runtime_context(
                state, f"{state.goal} {latest_user_message}"
            )
            if history and history[0].get("role") == "system":
                history[0] = {
                    **history[0],
                    "content": f"{history[0].get('content') or ''}\n\n{runtime_context}",
                }
            else:
                history.insert(0, {"role": "system", "content": runtime_context})

            try:
                response = await acompletion(
                    model=self.model_name,
                    messages=history,
                    tools=self._tool_schemas(),
                )
            except Exception as exc:
                state.record_step(
                    "model_request", success=False, error=str(exc), is_tool_call=False
                )
                state.status = TaskStatus.PAUSED
                await self.memory.checkpoint(
                    state, "Model request failed; task can be resumed."
                )
                logger.exception("Model request failed")
                return f"Task paused after a model request error: {exc}"

            state.record_usage(self.tracker.add_usage(response))
            await self.memory.checkpoint(state, "Model usage recorded.")
            message = response.choices[0].message
            tool_calls = self._serialize_tool_calls(
                getattr(message, "tool_calls", None)
            )

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

            for index, tool_call in enumerate(tool_calls):
                if state.should_pause():
                    return await self._pause_before_tool_calls(
                        tool_calls[index:], state, "Execution budget reached."
                    )

                await self._execute_tool_call(tool_call, state)

                if state.consecutive_failures > 0 or state.step_count % 5 == 0:
                    decision = await self._decide(state)
                    if decision.action == "pause":
                        state.status = TaskStatus.PAUSED
                        state.human_review_pauses += 1
                        await self.memory.checkpoint(state, decision.reason)
                        return self._pause_message(state, decision.reason)

    async def _ensure_state(self, goal: str) -> TaskState:
        state = await self.memory.load_state()
        resumable = {TaskStatus.RUNNING, TaskStatus.PAUSED, TaskStatus.FAILED}
        if state and state.status in resumable:
            state.status = TaskStatus.RUNNING
            self.tracker.restore(state)
            await self.memory.checkpoint(state, "Task resumed.")
            return state

        self.tracker = CostTracker()
        state = TaskState(
            session_id=self.session_id,
            goal=goal,
            max_steps=self.max_steps,
            cost_budget_usd=self.cost_budget_usd,
        )
        await self.memory.checkpoint(state, "Task started.")
        return state

    async def _decide(self, state: TaskState):
        try:
            return await self.policy.decide(state)
        except Exception as exc:
            logger.warning(
                "Decision policy failed; applying deterministic policy: %s", exc
            )
            return await HeuristicPolicy().decide(state)

    async def _execute_tool_call(
        self, tool_call: dict[str, Any], state: TaskState
    ) -> None:
        call_id = tool_call["id"]
        function = tool_call["function"]
        name = function["name"]

        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError as exc:
            result = f"Error: invalid tool arguments: {exc}"
            state.record_step(name, success=False, error=result)
            await self.memory.checkpoint(
                state,
                f"Recorded failed tool call {call_id}.",
                tool_results=[
                    {"name": name, "tool_call_id": call_id, "content": result}
                ],
            )
            return

        skill = self.skills.get(name)
        if skill is None:
            result = f"Error: unknown tool: {name}"
            state.record_step(name, success=False, error=result)
        else:
            try:
                if skill.requires_confirmation and not await skill.confirm(arguments):
                    result = "Error: tool execution was not approved."
                    state.record_step(name, success=False, error=result)
                else:
                    result = await skill.execute(**arguments)
                    success = not result.lower().startswith(("error", "failed"))
                    state.record_step(
                        name,
                        success=success,
                        error=None if success else result,
                    )
            except Exception as exc:
                result = f"Error: tool execution failed: {exc}"
                state.record_step(name, success=False, error=result)

        await self.memory.checkpoint(
            state,
            f"Recorded tool call {call_id}.",
            tool_results=[{"name": name, "tool_call_id": call_id, "content": result}],
        )

    async def _pause_before_tool_calls(
        self, tool_calls: list[dict[str, Any]], state: TaskState, reason: str
    ) -> str:
        state.status = TaskStatus.PAUSED
        state.human_review_pauses += 1
        results = [
            {
                "name": call["function"]["name"],
                "tool_call_id": call["id"],
                "content": f"Error: {reason} This tool call was not executed.",
            }
            for call in tool_calls
        ]
        await self.memory.checkpoint(state, reason, tool_results=results)
        return self._pause_message(state, reason)

    def _runtime_context(self, state: TaskState, query: str) -> str:
        cost = (
            f"${state.estimated_cost_usd:.4f} estimated"
            if state.cost_estimate_available and state.estimated_cost_usd is not None
            else "unavailable for this provider"
        )
        knowledge = self.knowledge.context(query)
        knowledge_block = (
            f"\nRelevant durable project knowledge:\n{knowledge}" if knowledge else ""
        )
        return (
            "Runtime state (authoritative and compact):\n"
            f"- task: {state.goal}\n"
            f"- status: {state.status.value}\n"
            f"- steps: {state.step_count}/{state.max_steps}\n"
            f"- cost: {cost}; budget: ${state.cost_budget_usd:.2f}\n"
            f"- consecutive failures: {state.consecutive_failures}\n"
            f"- last action: {state.last_action or 'none'}\n"
            f"- last error: {state.last_error or 'none'}\n"
            f"- tool calls/failures: {state.tool_calls}/{state.tool_failures}\n"
            "Continue toward the original goal. Prefer small, reversible actions."
            f"{knowledge_block}"
        )

    @staticmethod
    def _bounded_history(
        history: list[dict[str, Any]], max_messages: int = 40, max_chars: int = 24_000
    ) -> list[dict[str, Any]]:
        system = history[:1] if history and history[0].get("role") == "system" else []
        recent = history[len(system) :][-max_messages:]
        tool_result_ids = {
            message.get("tool_call_id")
            for message in recent
            if message.get("role") == "tool" and message.get("tool_call_id")
        }
        valid_call_ids = set()
        filtered = []
        for message in recent:
            calls = message.get("tool_calls") or []
            if message.get("role") == "assistant" and calls:
                ids = {call.get("id") for call in calls}
                if not ids or not ids.issubset(tool_result_ids):
                    continue
                valid_call_ids.update(ids)
            elif message.get("role") == "tool":
                if message.get("tool_call_id") not in tool_result_ids:
                    continue
            filtered.append(dict(message))

        filtered = [
            message
            for message in filtered
            if message.get("role") != "tool"
            or message.get("tool_call_id") in valid_call_ids
        ]
        bounded_system = [dict(system[0])] if system else []
        messages = bounded_system + filtered

        def clip(value: str, limit: int) -> str:
            if len(value) <= limit:
                return value
            marker = "\n[older context omitted]\n"
            if limit <= len(marker):
                return value[:limit]
            half = max(0, (limit - len(marker)) // 2)
            return value[:half] + marker + value[-half:]

        for message in messages:
            if isinstance(message.get("content"), str):
                message["content"] = clip(message["content"], 4_000)

        remaining = max_chars
        for message in messages:
            content = message.get("content")
            if not isinstance(content, str):
                continue
            allowance = min(len(content), remaining)
            message["content"] = clip(content, allowance) if allowance else ""
            remaining = max(0, remaining - len(message["content"]))
        return messages

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
