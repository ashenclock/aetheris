from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import BaseModel, Field

from nexus.core.agent import Agent
from nexus.core.policy import HeuristicPolicy
from nexus.core.state import TaskState
from nexus.skills.base_skill import BaseSkill

ROOT = Path(__file__).resolve().parents[1]
TASKS_FILE = Path(__file__).with_name("tasks.jsonl")


class NoteSchema(BaseModel):
    note: str = Field(description="Text for the controlled failure test")


class FlakyOnceSkill(BaseSkill):
    def __init__(self) -> None:
        self.attempts = 0

    @property
    def name(self) -> str:
        return "flaky_once"

    @property
    def description(self) -> str:
        return "Test-only tool that fails once before succeeding."

    @property
    def parameters_schema(self) -> type[BaseModel]:
        return NoteSchema

    async def execute(self, **kwargs) -> str:
        self.attempts += 1
        if self.attempts == 1:
            return "Error: controlled one-time failure."
        return "Recovered after one controlled failure."


class ScriptedModel:
    def __init__(self, task: dict) -> None:
        self.actions = task["actions"]
        self.final = task["final"]
        self.index = 0

    async def __call__(self, **kwargs):
        if self.index < len(self.actions):
            action = self.actions[self.index]
            self.index += 1
            call = SimpleNamespace(
                id=f"eval-call-{self.index}",
                type="function",
                function=SimpleNamespace(
                    name=action["tool"],
                    arguments=json.dumps(action["arguments"]),
                ),
            )
            message = SimpleNamespace(content=None, tool_calls=[call])
        else:
            message = SimpleNamespace(content=self.final, tool_calls=None)
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )
        return response


def load_tasks() -> list[dict]:
    return [
        json.loads(line) for line in TASKS_FILE.read_text().splitlines() if line.strip()
    ]


def resolve_paths(value, root: Path):
    if isinstance(value, dict):
        return {key: resolve_paths(item, root) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_paths(item, root) for item in value]
    if isinstance(value, str) and value.startswith("$file:"):
        return str(root / value.removeprefix("$file:"))
    return value


async def run_task(task: dict) -> dict:
    original_cwd = Path.cwd()
    with tempfile.TemporaryDirectory(prefix=f"aetheris-{task['id']}-") as temporary:
        root = Path(temporary)
        for relative_path, content in task.get("fixtures", {}).items():
            target = (root / relative_path).resolve()
            if root not in target.parents:
                raise ValueError(
                    f"Fixture path escapes the temporary directory: {relative_path}"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        os.chdir(root)
        try:
            session_id = task["id"]
            database = root / "agent.sqlite3"
            agent = Agent(
                model_name="mock/offline",
                db_path=str(database),
                session_id=session_id,
                max_steps=30,
                cost_budget_usd=0.10,
                policy=HeuristicPolicy(),
            )
            try:
                await agent.init()
                if any(action["tool"] == "flaky_once" for action in task["actions"]):
                    agent.skills["flaky_once"] = FlakyOnceSkill()

                approved = set(task.get("approve_tools", []))
                denied = set(task.get("deny_tools", []))
                for action in task["actions"]:
                    skill = agent.skills.get(action["tool"])
                    if skill and skill.requires_confirmation:
                        decision = (
                            action["tool"] in approved and action["tool"] not in denied
                        )
                        skill.confirm = AsyncMock(return_value=decision)

                if "interrupt_before_result" in task:
                    interrupted = resolve_paths(task["interrupt_before_result"], root)
                    call = {
                        "id": "interrupted-call",
                        "type": "function",
                        "function": {
                            "name": interrupted["tool"],
                            "arguments": json.dumps(interrupted["arguments"]),
                        },
                    }
                    state = TaskState(
                        session_id=session_id,
                        goal=task["goal"],
                        max_steps=30,
                        cost_budget_usd=0.10,
                    )
                    await agent.memory.checkpoint(
                        state, "Seeded interrupted state for evaluation."
                    )
                    await agent.memory.add_message("assistant", None, tool_calls=[call])

                model = ScriptedModel(
                    {**task, "actions": resolve_paths(task["actions"], root)}
                )
                started = time.perf_counter()
                with patch("nexus.core.agent.acompletion", new=model):
                    request = (
                        "Resume the task."
                        if "interrupt_before_result" in task
                        else task["goal"]
                    )
                    await agent.chat(request)
                elapsed_ms = (time.perf_counter() - started) * 1_000
                state = await agent.memory.load_state()
                assert state is not None

                checks = {
                    "status": state.status.value == task["expected_status"],
                    "tool_failures": state.tool_failures
                    == task.get("expected_tool_failures", state.tool_failures),
                    "recovered_calls": state.recovered_interrupted_calls
                    == task.get(
                        "expected_recovered_interrupted_calls",
                        state.recovered_interrupted_calls,
                    ),
                    "human_review_pauses": state.human_review_pauses
                    == task.get(
                        "expected_human_review_pauses", state.human_review_pauses
                    ),
                }
                return {
                    "id": task["id"],
                    "success": all(checks.values()),
                    "checks": checks,
                    "status": state.status.value,
                    "steps": state.step_count,
                    "tool_calls": state.tool_calls,
                    "tool_failures": state.tool_failures,
                    "recovered_interrupted_calls": state.recovered_interrupted_calls,
                    "successful_recovery": bool(
                        task.get("recovery_expected")
                        and state.status.value == "completed"
                        and (
                            state.tool_failures > 0
                            or state.recovered_interrupted_calls > 0
                        )
                    ),
                    "prompt_tokens": state.prompt_tokens,
                    "completion_tokens": state.completion_tokens,
                    "estimated_cost_usd": state.estimated_cost_usd,
                    "cost_estimate_available": state.cost_estimate_available,
                    "latency_ms": round(elapsed_ms, 2),
                    "human_review_pauses": state.human_review_pauses,
                    "checkpoints": state.checkpoint_count,
                }
            finally:
                await agent.close()
        finally:
            os.chdir(original_cwd)


async def run_suite() -> dict:
    results = []
    for task in load_tasks():
        results.append(await run_task(task))
    return {
        "mode": "offline scripted model; no external API calls",
        "tasks": results,
        "success_rate": sum(item["success"] for item in results) / len(results),
        "tool_calls": sum(item["tool_calls"] for item in results),
        "tool_failures": sum(item["tool_failures"] for item in results),
        "successful_recoveries": sum(item["successful_recovery"] for item in results),
        "prompt_tokens": sum(item["prompt_tokens"] for item in results),
        "completion_tokens": sum(item["completion_tokens"] for item in results),
        "estimated_cost_usd": None,
        "latency_ms": round(sum(item["latency_ms"] for item in results), 2),
        "human_review_pauses": sum(item["human_review_pauses"] for item in results),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Aetheris's deterministic offline task set."
    )
    parser.add_argument(
        "--output", type=Path, help="Optional path for the JSON summary"
    )
    args = parser.parse_args()
    summary = asyncio.run(run_suite())
    rendered = json.dumps(summary, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
