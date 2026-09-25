from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

import typer
from prompt_toolkit import PromptSession
from rich.console import Console
from rich.markdown import Markdown

from nexus.core.agent import Agent

app = typer.Typer(name="Aetheris", help="A small, resumable coding agent.")
console = Console()


def expand_file_tags(text: str) -> str:
    chunks = [text]
    for raw_path in re.findall(r"@([^\s]+)", text):
        path = Path(raw_path).expanduser()
        if path.is_file():
            chunks.append(f"\nFile: {path}\n```\n{path.read_text(encoding='utf-8')}\n```")
    return "\n".join(chunks)


@app.command()
def chat(
    model: str = typer.Option("ollama/llama3", "--model", "-m"),
    db_path: str = typer.Option("aetheris_memory.db", "--db", "-d"),
    max_steps: int = typer.Option(40, "--max-steps"),
    cost_budget: float = typer.Option(1.0, "--cost-budget"),
) -> None:
    asyncio.run(_chat(model, db_path, max_steps, cost_budget))


async def _chat(model: str, db_path: str, max_steps: int, cost_budget: float) -> None:
    session_id = "default"
    agent = Agent(model, db_path, session_id, max_steps, cost_budget)
    await agent.init()
    prompt = PromptSession()

    console.print(f"Aetheris ready. Model: [bold]{model}[/bold]. Type /help for commands.")
    try:
        while True:
            raw = (await prompt.prompt_async(f"[{session_id}:{os.path.basename(os.getcwd())}] > ")).strip()
            if not raw:
                continue
            if raw in {"/exit", "/quit"}:
                break
            if raw == "/help":
                console.print("/new NAME, /resume NAME, /status, /exit. Use @path to attach a text file.")
                continue
            if raw == "/status":
                state = await agent.memory.load_state()
                console.print(state.model_dump_json(indent=2) if state else "No active task state.")
                continue
            if raw.startswith("/new ") or raw.startswith("/resume "):
                session_id = raw.split(maxsplit=1)[1]
                await agent.close()
                agent = Agent(model, db_path, session_id, max_steps, cost_budget)
                await agent.init()
                console.print(f"Session: {session_id}")
                continue

            reply = await agent.chat(expand_file_tags(raw))
            console.print(Markdown(reply))
    finally:
        await agent.close()


@app.command()
def run(
    task: str = typer.Argument(...),
    model: str = typer.Option("ollama/llama3", "--model", "-m"),
    db_path: str = typer.Option("aetheris_memory.db", "--db", "-d"),
    session: str = typer.Option("run", "--session", "-s"),
    max_steps: int = typer.Option(40, "--max-steps"),
    cost_budget: float = typer.Option(1.0, "--cost-budget"),
) -> None:
    async def execute() -> None:
        agent = Agent(model, db_path, session, max_steps, cost_budget)
        await agent.init()
        try:
            console.print(Markdown(await agent.chat(expand_file_tags(task))))
        finally:
            await agent.close()

    asyncio.run(execute())


if __name__ == "__main__":
    app()
