from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

# Use LiteLLM's packaged price map so importing the agent does not fetch pricing over the network.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
from litellm import acompletion
from pydantic import ValidationError

from nexus.skills.base_skill import BaseSkill
from nexus.skills.delegate_task import DelegateTaskSkill
from nexus.skills.edit_file import EditFileSkill
from nexus.skills.list_dir import ListDirSkill
from nexus.skills.read_file import ReadFileSkill
from nexus.skills.recall import RecallSkill
from nexus.skills.remember import RememberSkill
from nexus.skills.run_command import RunCommandSkill
from nexus.skills.search_code import SearchCodeSkill
from nexus.skills.write_file import WriteFileSkill
from nexus.skills.use_skill import UseSkillSkill
from nexus.skills.web_search import WebSearchSkill
from nexus.skills.mcp_tool import MCPToolSkill
from nexus.mcp import MCPConnectionError, discover_tools
from nexus.project import validate_name

from .knowledge import KnowledgeStore
from .delegation import DelegationBudget
from .memory import SessionMemory
from .policy import DecisionPolicy, HeuristicPolicy, build_policy
from .state import TaskState, TaskStatus
from .tracker import CostTracker

logger = logging.getLogger("AetherisAgent")

SKILL_TYPES = (
    EditFileSkill,
    DelegateTaskSkill,
    ListDirSkill,
    ReadFileSkill,
    RecallSkill,
    RememberSkill,
    RunCommandSkill,
    SearchCodeSkill,
    WriteFileSkill,
    UseSkillSkill,
    WebSearchSkill,
)


class Agent:
    def __init__(
        self,
        model_name: str,
        db_path: str = "memory.db",
        session_id: str = "default",
        max_steps: int = 40,
        cost_budget_usd: float = 1.0,
        policy: DecisionPolicy | None = None,
        workspace_root: str | Path | None = None,
        enabled_skill_names: set[str] | None = None,
        subagent_model: str | None = None,
        subagent_budget_usd: float | None = None,
        max_subagents: int | None = None,
        context_char_budget: int | None = None,
        delegation_budget: DelegationBudget | None = None,
        mode: str = "execute",
        agent_profile: str | None = None,
    ):
        self.model_name = model_name
        self.session_id = session_id
        self.max_steps = max_steps
        self.cost_budget_usd = cost_budget_usd
        self.memory = SessionMemory(db_path=db_path, session_id=session_id)
        self.tracker = CostTracker(model_name)
        self.policy = policy or build_policy()
        self.workspace_root = (
            Path(workspace_root).expanduser().resolve()
            if workspace_root is not None
            else None
        )
        self.enabled_skill_names = enabled_skill_names
        self.subagent_model = (
            subagent_model or os.getenv("AETHERIS_SUBAGENT_MODEL") or model_name
        )
        self.subagent_budget_usd = (
            subagent_budget_usd
            if subagent_budget_usd is not None
            else min(cost_budget_usd * 0.25, 0.25)
        )
        self.max_subagents = (
            max_subagents
            if max_subagents is not None
            else int(os.getenv("AETHERIS_MAX_SUBAGENTS", "2"))
        )
        self.context_char_budget = context_char_budget or int(
            os.getenv("AETHERIS_CONTEXT_CHARS", "24000")
        )
        self.delegation_budget = delegation_budget or DelegationBudget(
            float(
                os.getenv("AETHERIS_SUBAGENT_BUDGET_USD", str(self.subagent_budget_usd))
            ),
            self.max_subagents,
        )
        if mode not in {"execute", "plan"}:
            raise ValueError("mode must be 'execute' or 'plan'")
        self.mode = mode
        self.agent_profile = validate_name(agent_profile) if agent_profile else None
        self.skills = self._load_skills()
        knowledge_root = (
            self.workspace_root / ".aetheris/wiki"
            if self.workspace_root
            else ".aetheris/wiki"
        )
        self.knowledge = KnowledgeStore(knowledge_root, self.workspace_root)
        self.system_prompt = self._load_system_prompt()
        if enabled_skill_names is not None:
            self.system_prompt += (
                "\n\nDeployment mode: the available tools are intentionally limited. "
                "Do not claim to edit, execute, or persist changes unless an available "
                "tool actually did so."
            )

    def _load_system_prompt(self) -> str:
        path = os.path.join(os.path.dirname(__file__), "prompts", "mega_prompt.md")
        try:
            with open(path, "r", encoding="utf-8") as handle:
                prompt = handle.read()
        except FileNotFoundError:
            prompt = "You are Aetheris, a careful software engineering agent."
        if self.mode == "plan":
            prompt += (
                "\n\nPLAN MODE: inspect and reason only. Do not call tools that write, "
                "edit, execute commands, install packages, or change Git. Return a "
                "structured plan with assumptions, files, validation steps, risks, "
                "and an explicit approval checkpoint before execution."
            )
        if self.agent_profile and self.workspace_root:
            agents_root = self.workspace_root / ".aetheris" / "agents"
            resolved_root = agents_root.resolve()
            try:
                resolved_root.relative_to(self.workspace_root)
            except ValueError as exc:
                raise ValueError(
                    "Agent profile directory escapes the workspace."
                ) from exc
            profile = agents_root / f"{self.agent_profile}.md"
            try:
                profile.resolve().relative_to(resolved_root)
            except ValueError as exc:
                raise ValueError(
                    "Agent profile file escapes its profile directory."
                ) from exc
            if profile.is_file():
                prompt += (
                    "\n\nPROJECT AGENT PROFILE (user configuration, not a policy override):\n"
                    + profile.read_text(encoding="utf-8")
                )
        return prompt

    def _load_skills(self) -> dict[str, BaseSkill]:
        skills: dict[str, BaseSkill] = {}
        plan_skills = {
            "delegate_task",
            "list_directory",
            "read_file",
            "recall",
            "search_code",
            "use_skill",
            "web_search",
        }
        for skill_type in SKILL_TYPES:
            if skill_type is DelegateTaskSkill:
                skill = skill_type(
                    workspace_root=self.workspace_root,
                    model_name=self.model_name,
                    db_path=self.memory.db_path,
                    parent_session=self.session_id,
                    cost_budget_usd=self.subagent_budget_usd,
                    subagent_model=self.subagent_model,
                    max_children=self.max_subagents,
                    delegation_budget=self.delegation_budget,
                    context_char_budget=min(self.context_char_budget, 12_000),
                    parent_memory=self.memory,
                )
            else:
                skill = skill_type(workspace_root=self.workspace_root)
            if (
                self.enabled_skill_names is None
                or skill.name in self.enabled_skill_names
            ):
                if self.mode == "plan" and skill.name not in plan_skills:
                    continue
                skills[skill.name] = skill
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
        await self._load_mcp_skills()
        if not await self.memory.get_history():
            await self.memory.add_message("system", self.system_prompt)
        state = await self.memory.load_state()
        if state is not None:
            self.tracker.restore(state)
            self.delegation_budget.restore(
                {
                    "total_usd": state.subagent_budget_usd,
                    "reserved_usd": state.subagent_budget_reserved_usd,
                    "spent_usd": state.subagent_budget_spent_usd,
                    "children_started": state.subagent_children_started,
                    "max_children": self.max_subagents,
                }
            )
            delegate_skill = self.skills.get("delegate_task")
            if isinstance(delegate_skill, DelegateTaskSkill):
                # Child session numbering is durable too; otherwise a resumed
                # parent could overwrite an earlier child transcript.
                delegate_skill._delegation_count = state.subagent_children_started

    async def _load_mcp_skills(self) -> None:
        """Expose configured MCP tools only in the top-level execute runtime."""
        if (
            self.mode == "plan"
            or self.enabled_skill_names is not None
            or self.workspace_root is None
        ):
            return
        try:
            discovered = await discover_tools(str(self.workspace_root))
        except (MCPConnectionError, OSError, ValueError) as exc:
            logger.warning("MCP discovery skipped: %s", exc)
            return
        for server, tool in discovered:
            skill = MCPToolSkill(server, tool, self.workspace_root)
            self.skills[skill.name] = skill

    async def close(self) -> None:
        await self.policy.close()

    async def chat(self, user_input: str) -> str:
        with self.memory.session_lock():
            return await self._chat_locked(user_input)

    async def _chat_locked(self, user_input: str) -> str:
        state = await self._ensure_state(user_input)
        await self.memory.recover_pending_tool_calls(state)
        await self.memory.add_message("user", user_input)

        while True:
            if state.should_pause():
                state.status = TaskStatus.PAUSED
                state.human_review_pauses += 1
                await self.memory.checkpoint(state, "Execution budget reached.")
                return self._pause_message(state, "Execution budget reached.")

            history = self._bounded_history(
                await self.memory.get_history(), max_chars=self.context_char_budget
            )
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
                response = await self._request_model(history)
            except asyncio.CancelledError:
                state.status = TaskStatus.PAUSED
                await self.memory.checkpoint(
                    state, "Task canceled while waiting for a model response."
                )
                raise
            except Exception as exc:
                state.record_step(
                    "model_request", success=False, error=str(exc), is_tool_call=False
                )
                state.status = TaskStatus.PAUSED
                await self.memory.checkpoint(
                    state, "Model request failed; task can be resumed."
                )
                logger.warning("Model request failed: %s", exc)
                return f"Task paused after a model request error: {exc}"

            state.record_usage(self.tracker.add_usage(response))
            await self.memory.checkpoint(state, "Model usage recorded.")
            try:
                message = response.choices[0].message
                tool_calls = self._serialize_tool_calls(
                    getattr(message, "tool_calls", None)
                )
            except (AttributeError, IndexError, TypeError, ValueError) as exc:
                error = f"Malformed model response: {exc}"
                state.record_step(
                    "model_response", success=False, error=error, is_tool_call=False
                )
                state.status = TaskStatus.PAUSED
                await self.memory.checkpoint(state, error)
                return self._pause_message(state, error)

            if not tool_calls:
                reply = message.content or ""
                if not reply.strip():
                    error = "Empty model response; task paused and can be resumed."
                    state.record_step(
                        "model_response", success=False, error=error, is_tool_call=False
                    )
                    state.status = TaskStatus.PAUSED
                    await self.memory.checkpoint(state, error)
                    return self._pause_message(state, error)
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

                try:
                    approval_denied = await self._execute_tool_call(tool_call, state)
                except asyncio.CancelledError:
                    state.status = TaskStatus.PAUSED
                    await self.memory.checkpoint(
                        state,
                        "Task canceled during tool execution; pending call will be recovered.",
                    )
                    raise

                if approval_denied:
                    state.status = TaskStatus.PAUSED
                    state.human_review_pauses += 1
                    remaining = tool_calls[index + 1 :]
                    results = [
                        {
                            "name": call["function"]["name"],
                            "tool_call_id": call["id"],
                            "content": "Error: execution paused after approval was denied; this tool call was not executed.",
                        }
                        for call in remaining
                    ]
                    await self.memory.checkpoint(
                        state,
                        "Tool approval denied; remaining calls were not executed.",
                        tool_results=results,
                    )
                    return self._pause_message(
                        state,
                        "Tool approval was denied; no further calls were executed.",
                    )

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
            # A resume may extend a previously reached step limit, but never
            # reduces it. Cost budgets remain authoritative in persisted state.
            if self.max_steps > state.max_steps:
                state.max_steps = self.max_steps
            state.status = TaskStatus.RUNNING
            self.tracker.restore(state)
            await self.memory.checkpoint(state, "Task resumed.")
            return state

        self.tracker = CostTracker(self.model_name)
        state = TaskState(
            session_id=self.session_id,
            goal=goal,
            max_steps=self.max_steps,
            cost_budget_usd=self.cost_budget_usd,
            subagent_budget_usd=self.delegation_budget.total_usd,
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

    async def _request_model(self, history: list[dict[str, Any]]) -> Any:
        # Keep provider-specific wire formats behind this boundary. The rest
        # of the runtime only handles the normalized chat/tool-call shape.
        if not self._uses_responses_api():
            return await acompletion(
                model=self.model_name,
                messages=history,
                tools=self._tool_schemas(),
            )
        return await self._request_responses_api(history)

    def _uses_responses_api(self) -> bool:
        return self.model_name.startswith("responses/")

    async def _request_responses_api(self, history: list[dict[str, Any]]) -> Any:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise RuntimeError(
                "The Responses API adapter requires the optional OpenAI SDK. "
                "Install with: pip install -e '.[transcription]'"
            ) from exc

        system = ""
        input_items: list[dict[str, Any]] = []
        for message in history:
            role = message.get("role")
            if role == "system":
                system = message.get("content") or ""
                continue
            if role == "assistant" and message.get("tool_calls"):
                for call in message["tool_calls"]:
                    function = call.get("function", {})
                    input_items.append(
                        {
                            "type": "function_call",
                            "call_id": call.get("id"),
                            "name": function.get("name", ""),
                            "arguments": function.get("arguments", "{}"),
                        }
                    )
                continue
            if role == "tool":
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": message.get("tool_call_id"),
                        "output": message.get("content") or "",
                    }
                )
                continue
            input_items.append({"role": role, "content": message.get("content") or ""})

        client = AsyncOpenAI()
        try:
            response = await client.responses.create(
                model=self.model_name.removeprefix("responses/"),
                instructions=system,
                input=input_items,
                tools=self._responses_tools(),
            )
        finally:
            await client.close()

        calls = []
        for item in getattr(response, "output", []) or []:
            if getattr(item, "type", None) != "function_call":
                continue
            calls.append(
                SimpleNamespace(
                    id=getattr(item, "call_id", None) or getattr(item, "id", ""),
                    function=SimpleNamespace(
                        name=getattr(item, "name", ""),
                        arguments=getattr(item, "arguments", "{}"),
                    ),
                )
            )
        usage = getattr(response, "usage", None)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=getattr(response, "output_text", "") or "",
                        tool_calls=calls or None,
                    )
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=getattr(usage, "input_tokens", 0) if usage else 0,
                completion_tokens=getattr(usage, "output_tokens", 0) if usage else 0,
            ),
        )

    def _responses_tools(self) -> list[dict[str, Any]]:
        tools = self._tool_schemas() or []
        return [
            {
                "type": "function",
                "name": tool["function"]["name"],
                "description": tool["function"]["description"],
                "parameters": tool["function"]["parameters"],
            }
            for tool in tools
        ]

    async def _execute_tool_call(
        self, tool_call: dict[str, Any], state: TaskState
    ) -> bool:
        # A tool result is persisted with the same call ID as the assistant
        # request, so a crash cannot turn an observation into a fake user turn.
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
            return False

        approval_denied = False
        skill = self.skills.get(name)
        if skill is None:
            result = f"Error: unknown tool: {name}"
            state.record_step(name, success=False, error=result)
        else:
            try:
                if not isinstance(skill, MCPToolSkill):
                    arguments = skill.parameters_schema.model_validate(
                        arguments
                    ).model_dump()
                approved = not skill.requires_confirmation
                if skill.requires_confirmation:
                    try:
                        approved = await skill.confirm(arguments)
                    except Exception:
                        approved = False
                if not approved:
                    result = "Error: tool execution was not approved or approval was unavailable."
                    state.record_step(name, success=False, error=result)
                    approval_denied = True
                else:
                    execution_arguments = dict(arguments)
                    if isinstance(skill, DelegateTaskSkill):
                        execution_arguments["_parent_state"] = state
                    result = await skill.execute(**execution_arguments)
                    if (
                        isinstance(skill, DelegateTaskSkill)
                        and skill.last_child_summary is not None
                    ):
                        state.record_subagent(skill.last_child_summary)
                        state.record_subagent_budget(self.delegation_budget.snapshot())
                    success = not result.lower().startswith(("error", "failed"))
                    state.record_step(
                        name,
                        success=success,
                        error=None if success else result,
                    )
            except ValidationError as exc:
                result = f"Error: invalid arguments for {name}: {exc}"
                state.record_step(name, success=False, error=result)
            except Exception as exc:
                result = f"Error: tool execution failed: {exc}"
                state.record_step(name, success=False, error=result)

        await self.memory.checkpoint(
            state,
            f"Recorded tool call {call_id}.",
            tool_results=[{"name": name, "tool_call_id": call_id, "content": result}],
        )
        return approval_denied

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
            f"- sub-agent sessions/failures: {state.subagent_sessions}/{state.subagent_failures}\n"
            f"- sub-agent tokens: {state.subagent_prompt_tokens}/{state.subagent_completion_tokens}\n"
            f"- sub-agent budget: ${state.subagent_budget_spent_usd:.4f} spent / ${state.subagent_budget_usd:.2f}\n"
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
            raw_arguments = getattr(function, "arguments", "{}")
            try:
                arguments = (
                    raw_arguments
                    if isinstance(raw_arguments, str)
                    else json.dumps(raw_arguments)
                )
            except (TypeError, ValueError):
                arguments = "null"
            call_id = getattr(call, "id", None)
            serialized.append(
                {
                    "id": call_id
                    if isinstance(call_id, str) and call_id
                    else uuid.uuid4().hex,
                    "type": "function",
                    "function": {
                        "name": getattr(function, "name", "") or "",
                        "arguments": arguments,
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
