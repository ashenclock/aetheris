from __future__ import annotations

import asyncio
import logging
import os
import secrets
import tempfile
from pathlib import Path
from typing import Any

import streamlit as st

from evals.run import run_suite
from nexus.core.knowledge import KnowledgeStore
from nexus.transcription import TranscriptionError, transcribe_file
from nexus.web.github import GitHubRepositoryError, download_public_repository
from nexus.skills.web_search import WebSearchSkill
from nexus.chat import ChatControls, COMMANDS
from nexus.core.events import format_event, redact
from nexus.web.session import run_turn
from dotenv import load_dotenv
from nexus.providers import PROVIDERS


logger = logging.getLogger(__name__)
LOCAL_MODE = os.getenv("AETHERIS_WEB_LOCAL") == "1"
if LOCAL_MODE:
    load_dotenv()


def _secret_or_env(name: str) -> str | None:
    try:
        value = st.secrets.get(name)
    except (FileNotFoundError, KeyError):
        value = None
    return value or os.getenv(name)


def _run(coroutine):
    return asyncio.run(coroutine)


for provider in PROVIDERS:
    for key in provider.env_vars:
        value = _secret_or_env(key)
        if value:
            os.environ.setdefault(key, value)


async def _search_web(query: str, domains: list[str]) -> str:
    """Run the bounded search skill behind an explicit UI button."""
    return await WebSearchSkill().execute(
        query=query,
        max_results=5,
        domains=domains,
    )


def _show_state(state: dict[str, Any] | None) -> None:
    if not state:
        return
    columns = st.columns(4)
    columns[0].metric("Status", state.get("status_label", state["status"]))
    columns[1].metric("Steps", f"{state['step_count']}/{state['max_steps']}")
    columns[2].metric("Tool calls", state["tool_calls"])
    columns[3].metric("Checkpoints", state["checkpoint_count"])
    with st.expander("Authoritative runtime state"):
        st.json(redact(state))


def _show_history(history: list[dict[str, Any]]) -> None:
    with st.expander("Persisted protocol trace"):
        for message in history[-12:]:
            role = message.get("role", "unknown")
            if role == "tool":
                label = f"tool:{message.get('name', 'unknown')} ({message.get('tool_call_id', '?')})"
            else:
                label = role
            st.code(
                redact(
                    f"{label}\n{message.get('content') or message.get('tool_calls') or ''}"
                )
            )


def _show_example_knowledge() -> None:
    """Display illustrative pages without writing them into agent memory."""
    st.info("Example knowledge graph — not facts extracted from this repository.")
    st.graphviz_chart(
        'digraph Example { rankdir=LR; node [shape=box]; "Runtime" -> "Checkpoints"; "Runtime" -> "Wiki"; "Wiki" -> "Checkpoints"; }'
    )
    for title, content in (
        (
            "Runtime",
            "An agent runtime controls tool execution and limits. See [[Checkpoints]] and [[Wiki]].",
        ),
        (
            "Checkpoints",
            "Saved task state supports resume. Interrupted side effects can remain ambiguous.",
        ),
        (
            "Wiki",
            "Markdown pages hold reviewed knowledge; links connect related topics such as [[Checkpoints]].",
        ),
    ):
        with st.expander(f"Example: {title}"):
            st.markdown(content)
    st.caption(
        "Illustrative only: these pages are not saved or included in model context."
    )


def _show_knowledge(root: Path) -> None:
    store = KnowledgeStore(root / ".aetheris/wiki", root)
    pages = store.pages()
    with st.expander("Knowledge base / wiki", expanded=True):
        st.caption(
            "Human-readable durable facts. Use [[page-topic]] inside Markdown to create a link."
        )
        if not pages:
            st.info(
                "This workspace has no wiki pages. Read-only GitHub mode cannot create them; local coding mode can use the approved remember tool."
            )
            _show_example_knowledge()
            return
        st.graphviz_chart(store.graph_dot(), use_container_width=True)
        for page in pages:
            with st.expander(page.stem.replace("-", " ").title()):
                st.markdown(redact(page.read_text(encoding="utf-8")))


st.set_page_config(page_title="Aetheris", page_icon="◈", layout="wide")
st.title("Aetheris")
st.caption(
    "One runtime, terminal and web chat. Local coding is opt-in; GitHub snapshots are read-only."
)

if "repo_root" not in st.session_state:
    st.session_state.repo_root = None
if "repo_name" not in st.session_state:
    st.session_state.repo_name = None
if "repo_temp" not in st.session_state:
    st.session_state.repo_temp = None
if "controls" not in st.session_state:
    st.session_state.controls = None

with st.sidebar:
    st.header("Demo controls")
    model = os.getenv("AETHERIS_MODEL", "openai/gpt-4o-mini")
    st.caption(f"Configured model: `{model}`")
    max_steps = st.slider("Maximum steps", min_value=1, max_value=100, value=40)
    if LOCAL_MODE:
        allowed = Path(os.getenv("AETHERIS_WEB_WORKSPACE", ".")).resolve()
        folder = st.text_input("Local workspace", value=str(allowed))
        if st.button("Open local workspace"):
            root = Path(folder).resolve()
            if not root.is_dir() or not root.is_relative_to(allowed):
                st.error(
                    "Choose an existing directory inside the configured workspace root."
                )
            else:
                st.session_state.repo_root = str(root)
                st.session_state.repo_name = str(root)
                st.session_state.controls = ChatControls(
                    root, str(root / "aetheris_memory.db"), model
                )
    st.caption(
        "The hosted demo exposes only read-only tools. Shell, write, edit, and push operations are intentionally unavailable."
    )

    st.subheader("Load a public repository")
    repository_url = st.text_input(
        "GitHub URL", value="https://github.com/ashenclock/aetheris"
    )
    branch = st.text_input("Branch (optional)", value="feature/long-horizon-agent")
    if st.button("Load repository", type="primary"):
        try:
            with st.spinner("Downloading a bounded public archive from GitHub..."):
                repository, root, temporary = download_public_repository(
                    repository_url, branch
                )
            old_temp = st.session_state.repo_temp
            if old_temp is not None:
                old_temp.cleanup()
            st.session_state.repo_temp = temporary
            st.session_state.repo_root = str(root)
            st.session_state.repo_name = (
                f"{repository.owner}/{repository.name}:{branch or 'default'}"
            )
            st.session_state.controls = ChatControls(
                root, str(root / ".aetheris/streamlit.sqlite3"), model, read_only=True
            )
            st.success(f"Loaded {st.session_state.repo_name}")
        except GitHubRepositoryError as exc:
            st.error(str(exc))

    if st.button("Run offline reliability demo"):
        with st.spinner("Running deterministic tests without an API key..."):
            summary = _run(
                run_suite(
                    {"edit-and-test", "resume-interrupted-call", "pause-for-approval"}
                )
            )
        st.session_state.offline_summary = summary

    st.subheader("API transcription")
    audio = st.file_uploader(
        "Audio file (max 25 MB)",
        type=["flac", "mp3", "mp4", "mpeg", "mpga", "m4a", "ogg", "wav", "webm"],
    )
    transcription_model = st.text_input("Transcription model", value="gpt-transcribe")
    if st.button("Transcribe with API") and audio is not None:
        api_key = _secret_or_env("OPENAI_API_KEY")
        if api_key:
            os.environ.setdefault("OPENAI_API_KEY", api_key)
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=Path(audio.name).suffix, delete=False
            ) as handle:
                handle.write(audio.getvalue())
                temporary_path = Path(handle.name)
            with st.spinner("Sending audio to the configured transcription API..."):
                transcription = transcribe_file(
                    temporary_path,
                    model=transcription_model,
                )
            st.session_state.transcription = transcription["text"]
        except TranscriptionError as exc:
            st.error(str(exc))
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    if st.session_state.get("transcription"):
        st.text_area("Latest transcript", st.session_state.transcription, height=180)

    st.subheader("Web search")
    st.caption(
        "The button is the approval gate. Results are bounded snippets and are not"
        " automatically sent to the agent."
    )
    web_query = st.text_input("Search query")
    web_domains = st.text_input("Domains (optional, comma-separated)")
    if st.button("Search the public web") and web_query.strip():
        brave_key = _secret_or_env("BRAVE_SEARCH_API_KEY")
        if brave_key:
            os.environ.setdefault("BRAVE_SEARCH_API_KEY", brave_key)
        domains = [item.strip() for item in web_domains.split(",") if item.strip()]
        with st.spinner("Searching the public web..."):
            search_result = _run(_search_web(web_query, domains))
        if search_result.startswith("Error:"):
            st.error(search_result)
        else:
            st.code(search_result)

if st.session_state.get("offline_summary"):
    summary = st.session_state.offline_summary
    st.subheader("Offline runtime evidence")
    st.success(
        f"{summary['success_rate']:.0%} success across {len(summary['tasks'])} deterministic cases"
    )
    st.json(summary)

if st.session_state.repo_root:
    root = Path(st.session_state.repo_root)
    controls = st.session_state.controls
    database = Path(controls.database)
    database.parent.mkdir(parents=True, exist_ok=True)
    controls.max_steps = max_steps
    _run(controls.memory.init_db())
    st.subheader(f"Repository: {st.session_state.repo_name}")
    st.info(
        (
            "GitHub snapshot: approved workspace writes enabled; no shell or MCP. Changes are temporary."
            if controls.writes_enabled
            else "Read-only GitHub snapshot. Use /write_enable for approved workspace edits; parent paths remain blocked."
        )
        if controls.read_only
        else "Local coding mode: approvals are not a sandbox. Use /permissions to inspect access."
    )
    st.caption(f"Session: {controls.session} · Model: {controls.model}")
    if st.session_state.get("notice"):
        st.info(st.session_state.pop("notice"))
    with st.expander("/ Commands"):
        st.text(
            "\n".join(
                f"{name} — {description}" for name, description in COMMANDS.items()
            )
        )
    _show_knowledge(root)
    with st.expander("State / database / checkpoint timeline"):
        state = _run(controls.memory.load_state())
        _show_state(state.public_payload() if state else None)
        trace = _run(controls.memory.inspect_trace())
        st.json(redact(trace))
        checkpoints = trace.get("checkpoints", [])
        if checkpoints:
            st.line_chart(checkpoints, x="timestamp", y="steps")
    # SQLite is the shared record: reopening the UI also sees CLI messages.
    history = _run(controls.memory.get_history())
    messages = [
        {"role": item["role"], "content": item["content"]}
        for item in history
        if item["role"] in {"user", "assistant"}
        and item.get("content")
        and not item.get("tool_calls")
    ]
    for message in messages:
        with st.chat_message(message["role"]):
            st.markdown(redact(message["content"]))
    last_turn = st.session_state.get("last_turn")
    if last_turn and last_turn["session"] == controls.session:
        with st.expander(
            "Last turn activity",
            expanded=bool(state and state.pending_approval),
        ):
            st.code("\n".join(last_turn["events"]))
        if not any(item["content"] == last_turn["reply"] for item in messages):
            st.info(redact(last_turn["reply"]))

    prompt = st.chat_input("Message or /command — /help /wiki /trace /resume")
    approval_id, approval = None, None
    saved = _run(controls.memory.load_state())
    if saved and saved.pending_approval:
        pending = saved.pending_approval
        st.warning(
            f"Approval required: {pending['function']['name']}. Nothing executed yet."
        )
        st.code(redact(pending["function"]["arguments"]))
        approve, reject = st.columns(2)
        if approve.button("Approve once"):
            approval_id, approval, prompt = (
                pending["id"],
                True,
                "Continue after explicit approval.",
            )
        if reject.button("Reject"):
            approval_id, approval, prompt = (
                pending["id"],
                False,
                "Reject the pending action.",
            )
    if prompt and prompt.startswith("/"):
        previous_selection = (controls.session, controls.model)
        result = _run(controls.command(prompt))
        st.info(result.text)
        if result.data is not None:
            if result.view == "wiki":
                if not result.data["pages"]:
                    _show_example_knowledge()
                else:
                    st.graphviz_chart(result.data["graph"])
                for page in result.data["pages"]:
                    st.markdown(redact(page["content"]))
            elif result.view in {"sessions", "providers"}:
                st.dataframe(result.data)
            else:
                st.json(redact(result.data))
        if result.exit:
            st.session_state.repo_root = None
            st.session_state.controls = None
            st.rerun()
        if (
            previous_selection != (controls.session, controls.model)
            and not result.prompt
        ):
            st.session_state.notice = result.text
            st.rerun()
        prompt = result.prompt
    if prompt:
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            with st.status("Aetheris activity", expanded=True) as activity:
                try:
                    events = []

                    def progress(event, details):
                        text = format_event(event, details)
                        events.append(text)
                        st.code(text)

                    answer, state, history = _run(
                        run_turn(controls, prompt, progress, approval_id, approval)
                    )
                    activity.update(
                        label="Turn finished — inspect state for completion or pause",
                        state="complete",
                    )
                    st.markdown(redact(answer))
                    _show_state(state)
                    _show_history(history)
                    st.session_state.last_turn = {
                        "session": controls.session,
                        "events": events[-100:],
                        "reply": answer,
                    }
                    st.rerun()
                except Exception:
                    request_id = secrets.token_hex(6)
                    logger.exception("Streamlit agent request %s failed", request_id)
                    st.error("The model request failed.")
                    st.caption(
                        f"Reference: {request_id}. Check server logs for details. "
                        "Configure the provider key in Streamlit Secrets or the "
                        "environment before using live analysis."
                    )
else:
    st.subheader("Start with the deterministic path")
    st.write(
        "Load a public GitHub repository, then ask a bounded read-only question. The offline demo is available in the sidebar and requires no API key."
    )
    st.code("streamlit run streamlit_app.py", language="bash")
