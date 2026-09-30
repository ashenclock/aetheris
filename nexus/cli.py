from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import typer
from prompt_toolkit import PromptSession
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from dotenv import load_dotenv

from nexus.api import serve as serve_api
from nexus.core.agent import Agent
from nexus.core.knowledge import KnowledgeStore
from nexus.core.memory import SessionMemory
from nexus.core.state import TaskState
from nexus.providers import discover_providers, provider_for_model
from nexus.transcription import TranscriptionError, transcribe_file

load_dotenv()
DEFAULT_MODEL = os.getenv("AETHERIS_MODEL", "ollama/llama3")

app = typer.Typer(
    name="Aetheris",
    help="A small, resumable coding agent with inspectable runtime state.",
    no_args_is_help=True,
)
console = Console()


def expand_file_tags(text: str) -> str:
    chunks = [text]
    for raw_path in re.findall(r"@([^\s]+)", text):
        path = Path(raw_path).expanduser()
        if path.is_file():
            chunks.append(
                f"\nFile: {path}\n```\n{path.read_text(encoding='utf-8')}\n```"
            )
    return "\n".join(chunks)


def _state_payload(state: TaskState | None) -> dict[str, Any] | None:
    return state.model_dump(mode="json") if state else None


def _print_state(state: TaskState | None) -> None:
    if state is None:
        console.print("[dim]No active task state.[/dim]")
        return
    table = Table(box=None, padding=(0, 1))
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Session", state.session_id)
    table.add_row("Status", state.status.value)
    table.add_row("Steps", f"{state.step_count}/{state.max_steps}")
    table.add_row("Tool calls", f"{state.tool_calls} ({state.tool_failures} failed)")
    table.add_row("Failures", str(state.consecutive_failures))
    table.add_row("Checkpoints", str(state.checkpoint_count))
    table.add_row(
        "Sub-agents",
        f"{state.subagent_sessions} ({state.subagent_failures} failed)",
    )
    table.add_row(
        "Sub-agent tokens",
        f"{state.subagent_prompt_tokens}/{state.subagent_completion_tokens}",
    )
    table.add_row(
        "Sub-agent budget",
        f"${state.subagent_budget_spent_usd:.4f} spent / ${state.subagent_budget_usd:.2f}",
    )
    table.add_row("Last action", state.last_action or "none")
    if state.last_error:
        table.add_row("Last error", state.last_error)
    console.print(table)


def _agent(
    *,
    model: str,
    db_path: str,
    session_id: str,
    max_steps: int,
    cost_budget: float,
    workspace: str,
) -> Agent:
    return Agent(
        model,
        db_path,
        session_id,
        max_steps,
        cost_budget,
        workspace_root=Path(workspace).expanduser().resolve(),
    )


@app.command()
def chat(
    model: str = typer.Option(DEFAULT_MODEL, "--model", "-m"),
    db_path: str = typer.Option("aetheris_memory.db", "--db", "-d"),
    session: str = typer.Option("default", "--session", "-s"),
    workspace: str = typer.Option(".", "--workspace", "-w"),
    max_steps: int = typer.Option(40, "--max-steps"),
    cost_budget: float = typer.Option(1.0, "--cost-budget"),
) -> None:
    asyncio.run(_chat(model, db_path, session, workspace, max_steps, cost_budget))


async def _chat(
    model: str,
    db_path: str,
    session_id: str,
    workspace: str,
    max_steps: int,
    cost_budget: float,
) -> None:
    agent = _agent(
        model=model,
        db_path=db_path,
        session_id=session_id,
        max_steps=max_steps,
        cost_budget=cost_budget,
        workspace=workspace,
    )
    await agent.init()
    prompt = PromptSession()
    console.print(
        Panel.fit(
            f"[bold]Aetheris ready[/bold]\nModel: {model}\nWorkspace: {Path(workspace).resolve()}\nSession: {session_id}\n[dim]/help /status /new NAME /resume NAME /exit[/dim]",
            border_style="cyan",
        )
    )
    try:
        while True:
            raw = (
                await prompt.prompt_async(
                    f"[aetheris:{session_id}:{os.path.basename(os.path.abspath(workspace))}] > "
                )
            ).strip()
            if not raw:
                continue
            if raw in {"/exit", "/quit"}:
                break
            if raw == "/help":
                console.print(
                    "[cyan]/new NAME[/cyan] starts a session, [cyan]/resume NAME[/cyan] switches to one, [cyan]/status[/cyan] prints state, [cyan]@path[/cyan] attaches a text file."
                )
                continue
            if raw == "/status":
                _print_state(await agent.memory.load_state())
                continue
            if raw.startswith("/new ") or raw.startswith("/resume "):
                session_id = raw.split(maxsplit=1)[1]
                await agent.close()
                agent = _agent(
                    model=model,
                    db_path=db_path,
                    session_id=session_id,
                    max_steps=max_steps,
                    cost_budget=cost_budget,
                    workspace=workspace,
                )
                await agent.init()
                console.print(f"Session switched to [bold]{session_id}[/bold].")
                continue

            with console.status("[bold cyan]Aetheris is working...", spinner="dots"):
                reply = await agent.chat(expand_file_tags(raw))
            console.print(
                Panel(Markdown(reply), title="Aetheris", border_style="green")
            )
            _print_state(await agent.memory.load_state())
    finally:
        await agent.close()


@app.command()
def run(
    task: str = typer.Argument(...),
    model: str = typer.Option(DEFAULT_MODEL, "--model", "-m"),
    db_path: str = typer.Option("aetheris_memory.db", "--db", "-d"),
    session: str = typer.Option("run", "--session", "-s"),
    workspace: str = typer.Option(".", "--workspace", "-w"),
    max_steps: int = typer.Option(40, "--max-steps"),
    cost_budget: float = typer.Option(1.0, "--cost-budget"),
    json_output: bool = typer.Option(
        False, "--json", help="Print reply and state as JSON."
    ),
) -> None:
    asyncio.run(
        _run(
            task,
            model,
            db_path,
            session,
            workspace,
            max_steps,
            cost_budget,
            json_output,
        )
    )


@app.command()
def resume(
    session: str = typer.Argument(..., help="Existing session ID to continue."),
    instruction: str = typer.Argument("Continue the task.", metavar="INSTRUCTION"),
    model: str = typer.Option(DEFAULT_MODEL, "--model", "-m"),
    db_path: str = typer.Option("aetheris_memory.db", "--db", "-d"),
    workspace: str = typer.Option(".", "--workspace", "-w"),
    max_steps: int = typer.Option(40, "--max-steps"),
    cost_budget: float = typer.Option(1.0, "--cost-budget"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Resume an existing task using the same database and session ID."""
    asyncio.run(
        _run(
            instruction,
            model,
            db_path,
            session,
            workspace,
            max_steps,
            cost_budget,
            json_output,
        )
    )


async def _run(
    task: str,
    model: str,
    db_path: str,
    session: str,
    workspace: str,
    max_steps: int,
    cost_budget: float,
    json_output: bool,
) -> None:
    agent = _agent(
        model=model,
        db_path=db_path,
        session_id=session,
        max_steps=max_steps,
        cost_budget=cost_budget,
        workspace=workspace,
    )
    await agent.init()
    try:
        with console.status("[bold cyan]Aetheris is working...", spinner="dots"):
            reply = await agent.chat(expand_file_tags(task))
        state = await agent.memory.load_state()
        if json_output:
            console.print_json(
                json.dumps({"reply": reply, "state": _state_payload(state)})
            )
            return
        console.print(Panel(Markdown(reply), title="Aetheris", border_style="green"))
        _print_state(state)
    finally:
        await agent.close()


@app.command()
def status(
    db_path: str = typer.Option("aetheris_memory.db", "--db", "-d"),
    session: str = typer.Option("run", "--session", "-s"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Inspect persisted state without starting a model request."""

    async def inspect() -> None:
        memory = SessionMemory(db_path=db_path, session_id=session)
        await memory.init_db()
        state = await memory.load_state()
        if json_output:
            console.print_json(json.dumps(_state_payload(state)))
        else:
            _print_state(state)
            latest = await memory.latest_checkpoint()
            if latest:
                console.print(f"Last checkpoint: [dim]{latest[1]}[/dim]")

    asyncio.run(inspect())


@app.command("skills")
def skills() -> None:
    """List the built-in skills and their safety boundary."""
    agent = Agent(model_name="mock/offline")
    table = Table(title="Aetheris skills")
    table.add_column("Skill", style="cyan")
    table.add_column("Approval", style="yellow")
    table.add_column("Purpose")
    for skill in agent.skills.values():
        table.add_row(
            skill.name,
            "required" if skill.requires_confirmation else "not required",
            skill.description,
        )
    console.print(table)


@app.command("providers")
def providers(json_output: bool = typer.Option(False, "--json")) -> None:
    """Show provider credentials detected locally without making API calls."""
    discovered = discover_providers()
    if json_output:
        console.print_json(json.dumps(discovered))
        return
    table = Table(title="Aetheris providers (discovery only)")
    table.add_column("Provider", style="cyan")
    table.add_column("Example model")
    table.add_column("Credentials")
    table.add_column("Status")
    for item in discovered:
        configured = bool(item["configured"])
        table.add_row(
            str(item["provider"]),
            str(item["model"]),
            str(item["credentials"]),
            "configured" if configured else "not configured",
        )
    console.print(table)
    console.print(
        "[dim]Discovery never calls a provider or switches models. Use an explicit "
        "--model for a live smoke test.[/dim]"
    )


@app.command()
def doctor(
    workspace: str = typer.Option(".", "--workspace", "-w"),
    model: str = typer.Option(DEFAULT_MODEL, "--model", "-m"),
) -> None:
    """Check local provider, workspace, optional packages, and child routing."""
    root = Path(workspace).expanduser().resolve()
    checks: list[tuple[str, str, str]] = []

    checks.append(("python", sys.version.split()[0], "ok"))
    checks.append(
        (
            "workspace",
            str(root),
            "ok"
            if root.exists() and os.access(root, os.W_OK)
            else "check path/writability",
        )
    )
    if model.startswith("responses/"):
        checks.append(
            (
                "OpenAI API key",
                "configured" if os.getenv("OPENAI_API_KEY") else "missing",
                "ok" if os.getenv("OPENAI_API_KEY") else "set OPENAI_API_KEY",
            )
        )
        checks.append(
            (
                "openai SDK",
                "installed" if importlib.util.find_spec("openai") else "missing",
                "ok"
                if importlib.util.find_spec("openai")
                else "pip install -e '.[transcription]'",
            )
        )
    elif model.startswith("ollama/"):
        installed = shutil.which("ollama") is not None
        checks.append(
            (
                "Ollama CLI",
                "installed" if installed else "missing",
                "ok" if installed else "install Ollama",
            )
        )
    else:
        checks.append(
            ("provider", provider_for_model(model), "verify provider credentials")
        )

    for item in discover_providers():
        if item["configured"]:
            checks.append(
                (
                    f"provider: {item['provider']}",
                    str(item["model"]),
                    "credential detected; no network call made",
                )
            )

    subagent_model = os.getenv("AETHERIS_SUBAGENT_MODEL") or model
    checks.append(
        (
            "sub-agent model",
            subagent_model,
            "inherits parent" if subagent_model == model else "explicit override",
        )
    )
    checks.append(
        (
            "sub-agent budget",
            os.getenv("AETHERIS_SUBAGENT_BUDGET_USD", "0.25"),
            "total shared reservation",
        )
    )
    checks.append(
        ("max sub-agents", os.getenv("AETHERIS_MAX_SUBAGENTS", "2"), "bounded")
    )
    checks.append(
        (
            "Streamlit",
            "installed" if importlib.util.find_spec("streamlit") else "missing",
            "optional web demo",
        )
    )
    checks.append(
        (
            "Docker CLI",
            "installed" if shutil.which("docker") else "missing",
            "daemon still needs to be running",
        )
    )

    table = Table(title="Aetheris doctor")
    table.add_column("Check", style="cyan")
    table.add_column("Value")
    table.add_column("Notes")
    for name, value, note in checks:
        table.add_row(name, value, note)
    console.print(table)


@app.command("wiki")
def wiki(
    workspace: str = typer.Option(".", "--workspace", "-w"),
    graph: bool = typer.Option(False, "--graph"),
) -> None:
    """Inspect the human-readable knowledge base and optional page links."""
    store = KnowledgeStore(Path(workspace).expanduser().resolve() / ".aetheris/wiki")
    pages = store.pages()
    if not pages:
        console.print("[dim]No durable wiki pages yet.[/dim]")
        return
    table = Table(title=f"Knowledge wiki: {store.root}")
    table.add_column("Page", style="cyan")
    table.add_column("Characters", justify="right")
    for page in pages:
        table.add_row(page.stem, str(len(page.read_text(encoding="utf-8"))))
    console.print(table)
    if graph:
        console.print(Panel(store.graph_dot(), title="Graphviz knowledge graph"))


@app.command()
def transcribe(
    audio: Path = typer.Argument(..., exists=True, readable=True),
    model: str = typer.Option("gpt-transcribe", "--model"),
    language: str | None = typer.Option(None, "--language"),
    prompt: str | None = typer.Option(None, "--prompt"),
    response_format: str = typer.Option("json", "--response-format"),
    output: Path | None = typer.Option(
        None, "--output", help="Write transcript text to a file."
    ),
) -> None:
    """Transcribe one audio file through the configured API provider."""
    try:
        result = transcribe_file(
            audio,
            model=model,
            language=language,
            prompt=prompt,
            response_format=response_format,
        )
    except TranscriptionError as exc:
        raise typer.BadParameter(str(exc)) from exc
    text = result["text"]
    if output:
        output.write_text(text + "\n", encoding="utf-8")
        console.print(f"Transcript written to {output}")
    else:
        console.print(text)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8787, "--port"),
    db_path: str = typer.Option("aetheris_memory.db", "--db", "-d"),
    workspace: str = typer.Option(".", "--workspace", "-w"),
    model: str = typer.Option(DEFAULT_MODEL, "--model", "-m"),
    max_steps: int = typer.Option(12, "--max-steps"),
    cost_budget: float = typer.Option(0.25, "--cost-budget"),
    read_only: bool = typer.Option(True, "--read-only/--allow-write"),
    api_token: str | None = typer.Option(
        None, "--api-token", envvar="AETHERIS_API_TOKEN"
    ),
) -> None:
    """Run the small authenticated HTTP adapter used by n8n and demos."""
    serve_api(
        host=host,
        port=port,
        db_path=db_path,
        workspace=workspace,
        model=model,
        max_steps=max_steps,
        cost_budget=cost_budget,
        read_only=read_only,
        api_token=api_token,
    )


if __name__ == "__main__":
    app()
