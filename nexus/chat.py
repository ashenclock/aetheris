"""Chat controls shared by the terminal and Streamlit; no model calls here."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from nexus.core.knowledge import KnowledgeStore
from nexus.core.memory import SessionMemory
from nexus.providers import discover_providers

COMMANDS = {
    "/help": "Show commands",
    "/status": "Inspect task state and budgets",
    "/resume": "List saved sessions",
    "/resume NAME": "Continue a saved task",
    "/new NAME": "Start or select a session",
    "/model MODEL": "Change model in a fresh session",
    "/provider": "List configured providers",
    "/permissions": "Show approval mode",
    "/permissions session": "Approve ordinary tools for this chat",
    "/permissions ask": "Ask before sensitive actions",
    "/wiki": "Read durable knowledge and graph",
    "/trace": "Inspect recent tool calls and checkpoints",
    "/skills": "List project skills",
    "/mcp": "List configured MCP servers",
    "/exit": "Leave chat",
}


@dataclass
class CommandResult:
    text: str = ""
    data: object = None
    view: str = "text"
    prompt: str | None = None
    exit: bool = False


@dataclass
class ChatControls:
    workspace: Path
    database: str
    model: str
    session: str = "chat"
    read_only: bool = False
    session_approval: bool = False
    max_steps: int = 40
    cost_budget: float = 1.0

    @property
    def memory(self) -> SessionMemory:
        return SessionMemory(self.database, self.session)

    async def command(self, text: str) -> CommandResult:
        command, _, argument = text.strip().partition(" ")
        argument = argument.strip()
        if command in {"/", "/help"}:
            return CommandResult(
                "\n".join(f"{name} — {help}" for name, help in COMMANDS.items())
            )
        if command in {"/exit", "/quit"}:
            return CommandResult("Session saved.", exit=True)
        if command == "/status":
            state = await self.memory.load_state()
            return CommandResult(
                "No task yet." if state is None else "Task state",
                data=state.public_payload() if state else None,
                view="state",
            )
        if command in {"/resume", "/sessions"}:
            if not argument:
                return CommandResult(
                    "Continue with /resume NAME.",
                    data=await self.memory.list_sessions(),
                    view="sessions",
                )
            memory = SessionMemory(self.database, argument)
            saved = await memory.load_state()
            if saved is None:
                return CommandResult(
                    f"Session '{argument}' not found. Use /resume to list saved sessions."
                )
            if saved.status.value == "completed":
                self.session = argument
                return CommandResult(
                    f"Session '{argument}' selected. The last task finished; type a new message."
                )
            self.session = argument
            return CommandResult(
                f"Resuming '{argument}' with a limit of {self.max_steps} steps.",
                prompt="Continue the saved task from its latest checkpoint.",
            )
        if command == "/new":
            if not argument:
                return CommandResult("Usage: /new NAME")
            self.session = argument
            return CommandResult(f"Session selected: {argument}")
        if command == "/provider":
            return CommandResult(
                f"Active model: {self.model}. Switch with /model MODEL.",
                data=discover_providers(),
                view="providers",
            )
        if command == "/model":
            if not argument:
                return CommandResult(
                    f"Active model: {self.model}. Usage: /model PROVIDER/MODEL"
                )
            self.model = argument
            self.session = f"chat-{uuid4().hex[:8]}"
            return CommandResult(
                f"Switched to {argument} in fresh session '{self.session}'. Previous sessions are preserved."
            )
        if command == "/permissions":
            if argument not in {"", "ask", "session"}:
                return CommandResult("Usage: /permissions [ask|session]")
            if self.read_only and argument == "session":
                return CommandResult(
                    "This hosted workspace is read-only. Local mode is required for edits and shell tools."
                )
            if argument:
                self.session_approval = argument == "session"
            mode = (
                "read-only"
                if self.read_only
                else (
                    "SESSION (automatic)"
                    if self.session_approval
                    else "ASK (per action)"
                )
            )
            return CommandResult(
                f"Approval mode: {mode}. Use /permissions session or /permissions ask. Sensitive reads and MCP always require explicit approval. Shell execution is not sandboxed."
            )
        if command in {"/wiki", "/kb"}:
            store = KnowledgeStore(self.workspace / ".aetheris/wiki", self.workspace)
            pages = [
                {"topic": path.stem, "content": path.read_text(encoding="utf-8")}
                for path in store.pages()
            ]
            return CommandResult(
                f"Wiki: {store.root}\n"
                + (
                    f"{len(pages)} pages; links use [[topic]]."
                    if pages
                    else "No wiki pages in this workspace. Read-only mode cannot create knowledge; use approved remember in local coding mode."
                ),
                data={"pages": pages, "graph": store.graph_dot()},
                view="wiki",
            )
        if command in {"/trace", "/db"}:
            return CommandResult(
                f"Database: {self.database}\nSession: {self.session}",
                data=await self.memory.inspect_trace(),
                view="trace",
            )
        if command == "/skills":
            files = sorted((self.workspace / ".aetheris/skills").glob("*/SKILL.md"))
            return CommandResult(
                "Project skills: "
                + (", ".join(path.parent.name for path in files) or "none")
            )
        if command == "/mcp":
            from nexus.project import load_mcp_config

            return CommandResult(
                "Configured MCP servers", data=load_mcp_config(self.workspace)
            )
        return CommandResult(f"Unknown chat command: {command}. Type /help.")
