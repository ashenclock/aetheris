from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner

from nexus.cli import _chat_model_error, app, expand_file_tags
from nexus.cli import _agent as make_agent
from nexus.core.state import TaskState, TaskStatus


class FakeAgent:
    def __init__(self, status: TaskStatus):
        self.memory = SimpleNamespace(
            load_state=AsyncMock(
                return_value=TaskState(
                    session_id="cli-task", goal="test", status=status
                )
            )
        )

    async def init(self):
        pass

    async def chat(self, _task):
        return "Task result"

    async def close(self):
        pass


def test_agent_factory_forwards_chat_progress_callback(tmp_path):
    from unittest.mock import patch

    def callback(*_args):
        pass

    with patch("nexus.cli.Agent") as agent_type:
        make_agent(
            model="mock/offline",
            db_path=str(tmp_path / "state.sqlite3"),
            session_id="factory-check",
            max_steps=3,
            cost_budget=0.1,
            workspace=str(tmp_path),
            progress_callback=callback,
        )

    assert agent_type.call_args.kwargs["progress_callback"] is callback


@pytest.mark.asyncio
async def test_file_tag_stays_inside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("workspace fixture", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("must not be attached", encoding="utf-8")

    expanded = await expand_file_tags(
        "Review @notes.txt and @../outside.txt", workspace
    )

    assert "workspace fixture" in expanded
    assert "must not be attached" not in expanded
    assert "outside workspace or missing" in expanded


@pytest.mark.asyncio
async def test_sensitive_file_tag_requires_approval(tmp_path, monkeypatch):
    from unittest.mock import patch

    (tmp_path / ".env").write_text("FAKE_KEY=fixture-only", encoding="utf-8")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    with patch("rich.prompt.Confirm.ask", return_value=False):
        expanded = await expand_file_tags("Read @.env", tmp_path)

    assert "FAKE_KEY" not in expanded
    assert "not approved" in expanded


@pytest.mark.asyncio
async def test_sensitive_file_tag_can_be_explicitly_approved(tmp_path, monkeypatch):
    from unittest.mock import patch

    (tmp_path / ".env").write_text("FAKE_KEY=fixture-only", encoding="utf-8")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    with patch("rich.prompt.Confirm.ask", return_value=True):
        expanded = await expand_file_tags("Read @.env", tmp_path)

    assert "FAKE_KEY=fixture-only" in expanded


def test_chat_requires_provider_key_before_opening_session(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    error = _chat_model_error("deepseek/deepseek-chat")

    assert error is not None
    assert "DEEPSEEK_API_KEY" in error
    assert "restart chat" in error


def test_chat_checks_local_model_is_installed(monkeypatch):
    from unittest.mock import patch

    monkeypatch.setattr("nexus.cli.shutil.which", lambda _name: "/usr/bin/ollama")
    with patch(
        "nexus.cli.subprocess.run",
        return_value=SimpleNamespace(
            returncode=0,
            stdout="NAME ID SIZE MODIFIED\nqwen2.5:3b abc 2GB today\n",
        ),
    ):
        error = _chat_model_error("ollama/llama3")

    assert error is not None
    assert "ollama pull llama3" in error


def test_chat_slash_commands_do_not_become_model_prompts(monkeypatch, tmp_path):
    class ChatAgent:
        def __init__(self, progress_callback=None):
            self.progress_callback = progress_callback
            self.messages = []
            self.memory = SimpleNamespace(
                load_state=AsyncMock(
                    return_value=TaskState(session_id="chat", goal="hello")
                )
            )

        async def init(self):
            pass

        async def chat(self, message):
            self.messages.append(message)
            if self.progress_callback:
                self.progress_callback(
                    "tool_start",
                    {"name": "read_file", "arguments": {"filepath": "README.md"}},
                )
                self.progress_callback("tool_done", {"name": "read_file"})
            return "Hello!"

        async def close(self):
            pass

    class Input:
        def __init__(self, **_kwargs):
            self.values = iter(
                ("/", "/resume", "/provider", "/typo", "Hi there", "/exit")
            )

        async def prompt_async(self, _prompt):
            return next(self.values)

    agents = []

    def make_agent(**kwargs):
        agent = ChatAgent(kwargs.get("progress_callback"))
        agents.append(agent)
        return agent

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr("nexus.cli._agent", make_agent)
    monkeypatch.setattr("nexus.cli.PromptSession", Input)

    result = CliRunner().invoke(
        app,
        [
            "chat",
            "--model",
            "deepseek/deepseek-chat",
            "--workspace",
            str(tmp_path),
            "--db",
            str(tmp_path / "chat.sqlite3"),
        ],
    )

    assert result.exit_code == 0
    assert agents[0].messages == ["Hi there"]
    assert "Continue with /resume NAME" in result.output
    assert "/wiki" in result.output
    assert "Active model:" in result.output
    assert "Unknown chat command: /typo" in result.output
    assert "→ read_file" in result.output
    assert "✓ read_file completed" in result.output


def test_chat_progress_displays_tool_and_safe_arguments(monkeypatch, tmp_path):
    class ChatAgent:
        def __init__(self, progress_callback):
            self.progress_callback = progress_callback
            self.memory = SimpleNamespace(load_state=AsyncMock(return_value=None))

        async def init(self):
            pass

        async def chat(self, _message):
            self.progress_callback(
                "tool_start",
                {"name": "run_command", "arguments": {"command": "pytest -q"}},
            )
            self.progress_callback("tool_done", {"name": "run_command"})
            return "Done"

        async def close(self):
            pass

    class Input:
        def __init__(self, **kwargs):
            self.values = iter(("run tests", "/exit"))

        async def prompt_async(self, _prompt):
            return next(self.values)

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr("nexus.cli.PromptSession", Input)
    monkeypatch.setattr(
        "nexus.cli._agent",
        lambda **kwargs: ChatAgent(
            kwargs.get("progress_callback", lambda *_args: None)
        ),
    )
    result = CliRunner().invoke(
        app,
        [
            "chat",
            "--model",
            "deepseek/deepseek-chat",
            "--workspace",
            str(tmp_path),
            "--db",
            str(tmp_path / "chat.sqlite3"),
        ],
    )
    assert result.exit_code == 0
    assert "→ run_command" in result.output


def test_model_command_switches_to_fresh_session(monkeypatch, tmp_path):
    class ChatAgent:
        def __init__(self):
            self.memory = SimpleNamespace(
                load_state=AsyncMock(
                    return_value=TaskState(session_id="chat", goal="hello")
                )
            )

        async def init(self):
            pass

        async def chat(self, _message):
            return "Hello!"

        async def close(self):
            pass

    class Input:
        def __init__(self, **_kwargs):
            self.values = iter(("/model openrouter/openai/gpt-4o-mini", "Hi", "/exit"))

        async def prompt_async(self, _prompt):
            return next(self.values)

    created = []

    def make_agent(**kwargs):
        created.append(kwargs)
        return ChatAgent()

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("nexus.cli._agent", make_agent)
    monkeypatch.setattr("nexus.cli.PromptSession", Input)

    result = CliRunner().invoke(
        app,
        [
            "chat",
            "--model",
            "deepseek/deepseek-chat",
            "--workspace",
            str(tmp_path),
            "--db",
            str(tmp_path / "chat.sqlite3"),
            "--session",
            "original",
        ],
    )

    assert result.exit_code == 0
    # Slash commands do not instantiate an agent or call a model.
    assert len(created) == 1
    assert created[0]["model"] == "openrouter/openai/gpt-4o-mini"
    assert created[0]["session_id"].startswith("chat-")
    assert created[0]["session_id"] != "original"
    assert "Previous sessions are preserved" in result.output


def test_run_returns_nonzero_when_task_is_paused(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "nexus.cli._agent", lambda **_kwargs: FakeAgent(TaskStatus.PAUSED)
    )
    result = CliRunner().invoke(
        app,
        [
            "run",
            "test",
            "--workspace",
            str(tmp_path),
            "--db",
            str(tmp_path / "state.sqlite3"),
        ],
    )

    assert result.exit_code == 2
    assert "Task result" in result.output
    assert "paused" in result.output


def test_run_returns_zero_for_model_final_response_but_labels_unverified(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "nexus.cli._agent", lambda **_kwargs: FakeAgent(TaskStatus.COMPLETED)
    )
    result = CliRunner().invoke(
        app,
        [
            "run",
            "test",
            "--workspace",
            str(tmp_path),
            "--db",
            str(tmp_path / "state.sqlite3"),
        ],
    )

    assert result.exit_code == 0
    assert "goal unverified" in result.output


@pytest.mark.parametrize(
    "existing_status",
    [TaskStatus.PAUSED, TaskStatus.FAILED, TaskStatus.COMPLETED],
)
def test_run_starts_fresh_session_when_session_exists(
    monkeypatch, tmp_path, existing_status
):
    created = []
    statuses = iter((existing_status, TaskStatus.PAUSED))

    def make_agent(**kwargs):
        created.append(kwargs["session_id"])
        return FakeAgent(next(statuses))

    monkeypatch.setattr("nexus.cli._agent", make_agent)
    result = CliRunner().invoke(
        app,
        [
            "run",
            "second task",
            "--workspace",
            str(tmp_path),
            "--db",
            str(tmp_path / "state.sqlite3"),
        ],
    )

    assert result.exit_code == 2
    assert created[0] == "run"
    assert created[1].startswith("run-")
    assert created[0] != created[1]
    assert "Existing session 'run' is preserved" in result.output


def test_resume_rejects_completed_session(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "nexus.cli._agent", lambda **_kwargs: FakeAgent(TaskStatus.COMPLETED)
    )
    result = CliRunner().invoke(
        app,
        [
            "resume",
            "finished-task",
            "--db",
            str(tmp_path / "state.sqlite3"),
        ],
    )

    assert result.exit_code == 2
    assert "no resumable task" in result.output
