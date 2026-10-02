from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import subprocess
from uuid import uuid4
import shutil
import sys
from pathlib import Path
from typing import Any, Callable

import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from dotenv import load_dotenv

from nexus.api import serve as serve_api
from nexus.chat import COMMANDS, ChatControls
from nexus.core.events import format_event, redact
from nexus.core.agent import Agent
from nexus.core.knowledge import KnowledgeStore
from nexus.core.memory import SessionBusyError, SessionMemory
from nexus.core.state import TaskState
from nexus.mcp import MCPConnectionError, MCPClient, configured_servers
from nexus.providers import PROVIDERS, discover_providers, provider_for_model
from nexus.project import (
    add_mcp_server,
    create_agent,
    create_skill,
    load_mcp_config,
    project_dir,
)
from nexus.transcription import TranscriptionError, transcribe_file
from nexus.skills.web_search import WebSearchSkill
from nexus.skills.read_file import ReadFileSkill

load_dotenv()
DEFAULT_MODEL = os.getenv("AETHERIS_MODEL", "ollama/llama3")

app = typer.Typer(
    name="Aetheris",
    help="A small, resumable coding agent with inspectable runtime state.",
    no_args_is_help=True,
)
agent_app = typer.Typer(help="Create and run project-local agent profiles.")
skill_app = typer.Typer(help="Create and inspect project-local skills.")
mcp_app = typer.Typer(help="Configure and validate project-local MCP servers.")
app.add_typer(agent_app, name="agent")
app.add_typer(skill_app, name="skill")
app.add_typer(mcp_app, name="mcp")
console = Console()
CHAT_COMMANDS = tuple(
    name.replace(" NAME", " ").replace(" MODEL", " ") for name in COMMANDS
)


def _chat_progress(event: str, details: dict[str, Any]) -> None:
    console.print(Text(format_event(event, details)))


async def expand_file_tags(text: str, workspace: str | Path) -> str:
    chunks = [text]
    reader = ReadFileSkill(workspace)
    for raw_path in re.findall(r"@([^\s]+)", text):
        path = reader.resolve_path(raw_path, must_exist=True)
        if path is None or not path.is_file():
            chunks.append(
                f"\nFile not attached (outside workspace or missing): {raw_path}"
            )
        elif not await reader.confirm({"filepath": raw_path}):
            chunks.append(
                f"\nFile not attached (sensitive read not approved): {raw_path}"
            )
        else:
            try:
                content = await reader.execute(filepath=raw_path)
            except (OSError, UnicodeError) as exc:
                content = f"Error reading file: {exc}"
            chunks.append(f"\nFile: {path}\n```\n{content}\n```")
    return "\n".join(chunks)


def _chat_model_error(model: str) -> str | None:
    provider = provider_for_model(model)
    credential_provider = "OpenAI" if provider == "OpenAI Responses" else provider
    credential_names = next(
        (spec.env_vars for spec in PROVIDERS if spec.name == credential_provider), ()
    )
    if credential_names and not any(
        os.getenv(name, "").strip() for name in credential_names
    ):
        names = " or ".join(credential_names)
        return f"{provider} needs {names}. Add it to .env, then restart chat."

    if provider != "Ollama":
        return None
    executable = shutil.which("ollama")
    if not executable:
        return "Ollama is not installed. Install it, or choose a configured API model with --model."
    try:
        result = subprocess.run(
            [executable, "list"], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return "Ollama is not responding. Start the Ollama app or run `ollama serve`."
    if result.returncode:
        return "Ollama is not responding. Start the Ollama app or run `ollama serve`."
    requested = model.split("/", maxsplit=1)[1]
    installed = {
        line.split()[0] for line in result.stdout.splitlines()[1:] if line.split()
    }
    if requested not in installed:
        return (
            f"Ollama model '{requested}' is not installed. Run `ollama pull {requested}`, "
            "or switch to a configured API model with `--model`."
        )
    return None


def _state_payload(state: TaskState | None) -> dict[str, Any] | None:
    return state.public_payload() if state else None


def _print_state(state: TaskState | None) -> None:
    if state is None:
        console.print("[dim]No active task state.[/dim]")
        return
    table = Table(box=None, padding=(0, 1))
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Session", state.session_id)
    table.add_row("Status", state.status_label)
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
    mode: str = "execute",
    agent_profile: str | None = None,
    progress_callback: Callable[[str, dict[str, Any]], None] | None = None,
    enabled_skill_names: set[str] | None = None,
) -> Agent:
    return Agent(
        model,
        db_path,
        session_id,
        max_steps,
        cost_budget,
        workspace_root=Path(workspace).expanduser().resolve(),
        mode=mode,
        agent_profile=agent_profile,
        progress_callback=progress_callback,
        enabled_skill_names=enabled_skill_names,
    )


@app.command()
def chat(
    model: str = typer.Option(DEFAULT_MODEL, "--model", "-m"),
    db_path: str = typer.Option("aetheris_memory.db", "--db", "-d"),
    session: str = typer.Option("default", "--session", "-s"),
    workspace: str = typer.Option(".", "--workspace", "-w"),
    max_steps: int = typer.Option(40, "--max-steps"),
    cost_budget: float = typer.Option(1.0, "--cost-budget"),
    agent_profile: str | None = typer.Option(
        None, "--agent", help="Project agent profile"
    ),
) -> None:
    error = _chat_model_error(model)
    if error:
        console.print(f"[yellow]{error}[/yellow]")
        raise typer.Exit(code=2)
    asyncio.run(
        _chat(model, db_path, session, workspace, max_steps, cost_budget, agent_profile)
    )


async def _chat(
    model: str,
    db_path: str,
    session_id: str,
    workspace: str,
    max_steps: int,
    cost_budget: float,
    agent_profile: str | None = None,
) -> None:
    controls = ChatControls(
        Path(workspace).resolve(),
        db_path,
        model,
        session_id,
        max_steps=max_steps,
        cost_budget=cost_budget,
    )
    await controls.memory.init_db()
    prompt = PromptSession(
        completer=WordCompleter(CHAT_COMMANDS, ignore_case=True, sentence=True),
        complete_while_typing=True,
    )
    console.print(
        Panel(
            f"Aetheris chat\nModel: {model}\nWorkspace: {controls.workspace}\n"
            f"Session: {session_id}\nType a message, or / for commands. /wiki shows memory; /trace shows activity.",
            border_style="cyan",
        )
    )
    while True:
        try:
            raw = (
                await prompt.prompt_async(f"You ({controls.workspace.name}) > ")
            ).strip()
        except (EOFError, KeyboardInterrupt):
            console.print("Leaving chat. Your session is saved.")
            return
        except OSError as exc:
            console.print(
                Text(
                    f"Interactive input unavailable: {exc}. Use a terminal.",
                    style="yellow",
                )
            )
            return
        if not raw:
            continue
        if raw.startswith("/"):
            if raw.startswith("/model "):
                error = _chat_model_error(raw.split(maxsplit=1)[1])
                if error:
                    console.print(Text(error, style="yellow"))
                    continue
            result = await controls.command(raw)
            console.print(Text(result.text))
            if result.view == "sessions":
                table = Table("Session", "Status", "Updated", title="Saved sessions")
                for item in result.data:
                    table.add_row(item["session"], item["status"], item["updated"])
                console.print(table)
            elif result.view == "wiki":
                for page in result.data["pages"]:
                    console.print(
                        Panel(Markdown(redact(page["content"])), title=page["topic"])
                    )
                console.print(Text(result.data["graph"]))
            elif result.data is not None:
                console.print_json(data=redact(result.data))
            if result.exit:
                console.print("Leaving chat. Your session is saved.")
                return
            if result.prompt is None:
                continue
            raw = result.prompt
        agent = _agent(
            model=controls.model,
            db_path=db_path,
            session_id=controls.session,
            max_steps=controls.max_steps,
            cost_budget=controls.cost_budget,
            workspace=workspace,
            agent_profile=agent_profile,
            progress_callback=_chat_progress,
            enabled_skill_names=controls.enabled_tools,
        )
        agent.session_approval = controls.session_approval
        try:
            await agent.init()
            reply = await agent.chat(await expand_file_tags(raw, workspace))
            console.print(
                Panel(Markdown(reply), title="Aetheris", border_style="green")
            )
            _print_state(await agent.memory.load_state())
        except SessionBusyError as exc:
            console.print(Text(f"{exc} Check status or retry later.", style="yellow"))
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
    agent_profile: str | None = typer.Option(
        None, "--agent", help="Project agent profile"
    ),
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
            "execute",
            agent_profile,
            fresh_if_existing=True,
        )
    )


@app.command()
def plan(
    task: str = typer.Argument(...),
    model: str = typer.Option(DEFAULT_MODEL, "--model", "-m"),
    db_path: str = typer.Option("aetheris_memory.db", "--db", "-d"),
    session: str = typer.Option("plan", "--session", "-s"),
    workspace: str = typer.Option(".", "--workspace", "-w"),
    max_steps: int = typer.Option(12, "--max-steps"),
    cost_budget: float = typer.Option(0.25, "--cost-budget"),
    output: Path | None = typer.Option(
        None, "--output", help="Save plan Markdown here"
    ),
    agent_profile: str | None = typer.Option(
        None, "--agent", help="Project agent profile"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Print reply and state as JSON."
    ),
) -> None:
    """Inspect and design a task without write or shell tools."""
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
            "plan",
            agent_profile,
            output,
            True,
        )
    )


@app.command()
def goal(
    task: str = typer.Argument(...),
    plan_first: bool = typer.Option(True, "--plan-first/--execute-now"),
    model: str = typer.Option(DEFAULT_MODEL, "--model", "-m"),
    db_path: str = typer.Option("aetheris_memory.db", "--db", "-d"),
    session: str = typer.Option("goal", "--session", "-s"),
    workspace: str = typer.Option(".", "--workspace", "-w"),
    max_steps: int = typer.Option(40, "--max-steps"),
    cost_budget: float = typer.Option(1.0, "--cost-budget"),
    agent_profile: str | None = typer.Option(
        None, "--agent", help="Project agent profile"
    ),
) -> None:
    """Start a goal; plan-first is the safe default."""
    if plan_first:
        plan_path = project_dir(workspace) / "plans" / f"{session}.md"
        asyncio.run(
            _run(
                task,
                model,
                db_path,
                f"{session}:plan",
                workspace,
                min(max_steps, 12),
                min(cost_budget, 0.25),
                False,
                "plan",
                agent_profile,
                plan_path,
                True,
            )
        )
        console.print(
            f"[yellow]Plan saved to {plan_path}. Review it, then run:[/yellow]"
        )
        console.print(
            f"aetheris run {task!r} --session {session} --workspace {workspace!r}"
        )
        return
    asyncio.run(
        _run(
            task,
            model,
            db_path,
            session,
            workspace,
            max_steps,
            cost_budget,
            False,
            "execute",
            agent_profile,
            None,
            True,
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
    agent_profile: str | None = typer.Option(
        None, "--agent", help="Project agent profile"
    ),
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
            "execute",
            agent_profile,
            resume_only=True,
        )
    )


@app.command("search")
def search(
    query: str = typer.Argument(..., help="Question or keywords to search for."),
    max_results: int = typer.Option(5, "--max-results", min=1, max=8),
    region: str = typer.Option("us-en", "--region"),
    timelimit: str | None = typer.Option(None, "--timelimit"),
    backend: str = typer.Option("auto", "--backend"),
    domain: list[str] = typer.Option(
        [], "--domain", help="Restrict results to this domain; repeatable."
    ),
) -> None:
    """Search the public web through the approval-gated native search skill."""

    async def search_once() -> str:
        skill = WebSearchSkill()
        arguments = {
            "query": query,
            "max_results": max_results,
            "region": region,
            "timelimit": timelimit,
            "backend": backend,
            "domains": domain,
        }
        if not await skill.confirm(arguments):
            console.print(
                "Web search was not run: interactive approval is unavailable or was denied."
            )
            raise typer.Exit(code=1)
        return await skill.execute(**arguments)

    result = asyncio.run(search_once())
    if result.startswith("Error:"):
        console.print(Text(result, style="red"))
        raise typer.Exit(code=1)
    console.print(Panel(Text(result), title="External web search", border_style="cyan"))


async def _run(
    task: str,
    model: str,
    db_path: str,
    session: str,
    workspace: str,
    max_steps: int,
    cost_budget: float,
    json_output: bool,
    mode: str = "execute",
    agent_profile: str | None = None,
    output: Path | None = None,
    fresh_if_existing: bool = False,
    resume_only: bool = False,
) -> None:
    agent = _agent(
        model=model,
        db_path=db_path,
        session_id=session,
        max_steps=max_steps,
        cost_budget=cost_budget,
        workspace=workspace,
        mode=mode,
        agent_profile=agent_profile,
    )
    await agent.init()
    previous_state = await agent.memory.load_state()
    if resume_only and (
        previous_state is None or previous_state.status.value == "completed"
    ):
        await agent.close()
        console.print(
            f"Session '{session}' has no resumable task. Start a new task with `aetheris run`."
        )
        raise typer.Exit(code=2)
    if fresh_if_existing and previous_state is not None:
        await agent.close()
        previous_session = session
        session = f"{session}-{uuid4().hex[:8]}"
        console.print(
            f"Existing session '{previous_session}' is preserved; starting a new task in '{session}'."
        )
        agent = _agent(
            model=model,
            db_path=db_path,
            session_id=session,
            max_steps=max_steps,
            cost_budget=cost_budget,
            workspace=workspace,
            mode=mode,
            agent_profile=agent_profile,
        )
        await agent.init()
    try:
        try:
            with console.status("[bold cyan]Aetheris is working...", spinner="dots"):
                reply = await agent.chat(await expand_file_tags(task, workspace))
        except SessionBusyError as exc:
            if json_output:
                console.print_json(json.dumps({"error": str(exc), "session": session}))
            else:
                console.print(f"[yellow]{exc} Check status or retry later.[/yellow]")
            raise typer.Exit(code=2) from exc
        state = await agent.memory.load_state()
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(reply + "\n", encoding="utf-8")
            console.print(f"Plan written to {output}")
        if json_output:
            console.print_json(
                json.dumps({"reply": reply, "state": _state_payload(state)})
            )
        else:
            console.print(
                Panel(Markdown(reply), title="Aetheris", border_style="green")
            )
            _print_state(state)
        if state is not None and state.status.value != "completed":
            raise typer.Exit(code=2)
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


@skill_app.command("create")
def create_skill_command(
    name: str = typer.Argument(...),
    workspace: str = typer.Option(".", "--workspace", "-w"),
    description: str = typer.Option(
        "A project-specific bounded workflow", "--description"
    ),
) -> None:
    """Scaffold a reviewable project skill Markdown file."""
    try:
        path = create_skill(workspace, name, description)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"Created skill [cyan]{name}[/cyan]: {path}")


@skill_app.command("list")
def list_project_skills(
    workspace: str = typer.Option(".", "--workspace", "-w"),
) -> None:
    """List project-local skills without loading them into the model."""
    root = project_dir(workspace) / "skills"
    paths = sorted(root.glob("*/SKILL.md")) if root.exists() else []
    if not paths:
        console.print("[dim]No project skills.[/dim]")
        return
    for path in paths:
        console.print(f"[cyan]{path.parent.name}[/cyan]  {path}")


@agent_app.command("create")
def create_agent_command(
    name: str = typer.Argument(...),
    workspace: str = typer.Option(".", "--workspace", "-w"),
    description: str = typer.Option("A project-specific specialist", "--description"),
) -> None:
    """Scaffold a reviewable project agent profile."""
    try:
        path = create_agent(workspace, name, description)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"Created agent [cyan]{name}[/cyan]: {path}")


@agent_app.command("list")
def list_project_agents(
    workspace: str = typer.Option(".", "--workspace", "-w"),
) -> None:
    """List project-local agent profiles."""
    root = project_dir(workspace) / "agents"
    paths = sorted(root.glob("*.md")) if root.exists() else []
    if not paths:
        console.print("[dim]No project agents.[/dim]")
        return
    for path in paths:
        console.print(f"[cyan]{path.stem}[/cyan]  {path}")


@mcp_app.command("add")
def add_mcp_command(
    name: str = typer.Argument(...),
    command: str = typer.Option(..., "--command", help="Local stdio server command"),
    workspace: str = typer.Option(".", "--workspace", "-w"),
    env: list[str] | None = typer.Option(
        None, "--env", help="Allowed env variable name; repeatable"
    ),
) -> None:
    """Register a local MCP stdio server without starting it."""
    try:
        path = add_mcp_server(workspace, name, command, env=env)
    except (ValueError, json.JSONDecodeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"Registered MCP server [cyan]{name}[/cyan] in {path}")


@mcp_app.command("list")
def list_mcp_command(
    workspace: str = typer.Option(".", "--workspace", "-w"),
) -> None:
    """List configured MCP servers; this does not connect to them."""
    try:
        servers = load_mcp_config(workspace).get("mcpServers", {})
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not servers:
        console.print("[dim]No MCP servers configured.[/dim]")
        return
    for name, config in servers.items():
        command = " ".join(
            [str(config.get("command", "")), *map(str, config.get("args", []))]
        ).strip()
        console.print(
            f"[cyan]{name}[/cyan]  {'enabled' if config.get('enabled', True) else 'disabled'}  {command}"
        )


@mcp_app.command("validate")
def validate_mcp_command(
    workspace: str = typer.Option(".", "--workspace", "-w"),
) -> None:
    """Validate MCP JSON and command metadata without launching servers."""
    try:
        config = load_mcp_config(workspace)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(
        f"[green]Valid MCP configuration:[/green] {len(config.get('mcpServers', {}))} server(s)"
    )


@mcp_app.command("test")
def test_mcp_command(
    workspace: str = typer.Option(".", "--workspace", "-w"),
) -> None:
    """Connect to each enabled local MCP server and list its tools."""

    async def probe() -> list[tuple[str, list[str], str | None]]:
        results = []
        for server in configured_servers(workspace):
            try:
                tools = await MCPClient(
                    server, str(Path(workspace).resolve())
                ).list_tools()
                results.append(
                    (server.name, [str(item.get("name")) for item in tools], None)
                )
            except (MCPConnectionError, OSError, ValueError) as exc:
                results.append((server.name, [], str(exc)))
        return results

    results = asyncio.run(probe())
    if not results:
        console.print("[dim]No enabled MCP servers configured.[/dim]")
        return
    failed = False
    for name, tools, error in results:
        if error:
            failed = True
            console.print(f"[red]FAIL[/red] {name}: {error}")
        else:
            console.print(f"[green]OK[/green] {name}: {', '.join(tools) or 'no tools'}")
    if failed:
        raise typer.Exit(code=1)


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
    workspace_root = Path(workspace).expanduser().resolve()
    store = KnowledgeStore(workspace_root / ".aetheris/wiki", workspace_root)
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
    read_only: bool = typer.Option(
        True,
        "--read-only/--allow-write",
        help="The HTTP adapter has no remote approval queue; --allow-write is rejected.",
    ),
    api_token: str | None = typer.Option(
        None, "--api-token", envvar="AETHERIS_API_TOKEN"
    ),
) -> None:
    """Run the small authenticated HTTP adapter used by n8n and demos."""
    if not read_only:
        raise typer.BadParameter(
            "The HTTP API has no remote approval workflow; use the interactive CLI for writes."
        )
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
