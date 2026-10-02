from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_read_only_home_page_renders_without_provider_keys_or_network():
    app = AppTest.from_file(Path(__file__).parents[1] / "streamlit_app.py").run()

    assert not app.exception
    assert [title.value for title in app.title] == ["Aetheris"]
    assert not app.error


def test_local_ui_commands_and_trace_render_without_model(monkeypatch, tmp_path):
    monkeypatch.setenv("AETHERIS_WEB_LOCAL", "1")
    monkeypatch.setenv("AETHERIS_WEB_WORKSPACE", str(tmp_path))
    app = AppTest.from_file(Path(__file__).parents[1] / "streamlit_app.py").run()
    next(
        button for button in app.button if button.label == "Open local workspace"
    ).click().run()
    assert not app.exception
    assert app.chat_input
    for command in (
        "/",
        "/status",
        "/resume",
        "/wiki",
        "/trace",
        "/permissions session",
        "/permissions ask",
    ):
        app.chat_input[0].set_value(command).run()
        assert not app.exception, command
        assert not app.error, command
    assert app.session_state["controls"].session_approval is False


def test_empty_wiki_shows_example_without_creating_facts(monkeypatch, tmp_path):
    monkeypatch.setenv("AETHERIS_WEB_LOCAL", "1")
    monkeypatch.setenv("AETHERIS_WEB_WORKSPACE", str(tmp_path))
    app = AppTest.from_file(Path(__file__).parents[1] / "streamlit_app.py").run()
    next(
        button for button in app.button if button.label == "Open local workspace"
    ).click().run()
    app.chat_input[0].set_value("/wiki").run()
    assert not app.exception
    assert any("Example knowledge graph" in item.value for item in app.info)
    assert app.get("graphviz_chart")
    assert not list(tmp_path.rglob("*.md"))


def test_local_ui_tool_activity_and_approval_button(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import json

    monkeypatch.setenv("AETHERIS_WEB_LOCAL", "1")
    monkeypatch.setenv("AETHERIS_WEB_WORKSPACE", str(tmp_path))
    call = SimpleNamespace(
        id="ui-write",
        function=SimpleNamespace(
            name="write_file",
            arguments=json.dumps({"filepath": "created.txt", "content": "approved"}),
        ),
    )

    def response(content=None, calls=None):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=content, tool_calls=calls)
                )
            ],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )

    model = AsyncMock(
        side_effect=[
            response("I will create one file.", [call]),
            response("File created."),
        ]
    )
    monkeypatch.setattr("nexus.core.agent.acompletion", model)
    app = AppTest.from_file(Path(__file__).parents[1] / "streamlit_app.py").run()
    next(
        button for button in app.button if button.label == "Open local workspace"
    ).click().run()
    app.chat_input[0].set_value("Create one file").run()
    assert not app.exception
    assert not (tmp_path / "created.txt").exists()
    assert any("write_file" in item.value for item in app.code)
    next(
        button for button in app.button if button.label == "Approve once"
    ).click().run()
    assert not app.exception
    assert (tmp_path / "created.txt").read_text() == "approved"
    assert model.await_count == 2


def test_ui_restores_cli_history_and_switches_sessions_immediately(
    monkeypatch, tmp_path
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from typer.testing import CliRunner
    from nexus.cli import app as cli

    model = AsyncMock(
        return_value=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="Saved CLI answer", tool_calls=None)
                )
            ],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )
    )
    monkeypatch.setattr("nexus.core.agent.acompletion", model)
    result = CliRunner().invoke(
        cli,
        [
            "run",
            "Explain this workspace",
            "--model",
            "mock/offline",
            "--workspace",
            str(tmp_path),
            "--db",
            str(tmp_path / "aetheris_memory.db"),
            "--session",
            "from-cli",
        ],
    )
    assert result.exit_code == 0, result.output
    monkeypatch.setenv("AETHERIS_WEB_LOCAL", "1")
    monkeypatch.setenv("AETHERIS_WEB_WORKSPACE", str(tmp_path))
    app = AppTest.from_file(Path(__file__).parents[1] / "streamlit_app.py").run()
    next(
        button for button in app.button if button.label == "Open local workspace"
    ).click().run()
    app.chat_input[0].set_value("/resume from-cli").run()
    assert not app.exception
    assert app.session_state["controls"].session == "from-cli"
    assert any(item.value == "Saved CLI answer" for item in app.markdown)
    assert model.await_count == 1
    app.chat_input[0].set_value("/new empty-session").run()
    assert not app.exception
    assert not app.chat_message
    assert app.session_state["controls"].session == "empty-session"


def test_wiki_links_render_as_graph(monkeypatch, tmp_path):
    from nexus.core.knowledge import KnowledgeStore

    store = KnowledgeStore(tmp_path / ".aetheris/wiki", tmp_path)
    store.remember("architecture", "Uses [[testing]].", source="fixture")
    store.remember("testing", "Run pytest.", source="fixture")
    monkeypatch.setenv("AETHERIS_WEB_LOCAL", "1")
    monkeypatch.setenv("AETHERIS_WEB_WORKSPACE", str(tmp_path))
    app = AppTest.from_file(Path(__file__).parents[1] / "streamlit_app.py").run()
    next(
        button for button in app.button if button.label == "Open local workspace"
    ).click().run()
    assert not app.exception
    assert app.get("graphviz_chart")
    assert any("Uses [[testing]]" in item.value for item in app.markdown)
