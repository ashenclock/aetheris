from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Any

import streamlit as st

from evals.run import run_suite
from nexus.core.agent import Agent
from nexus.core.knowledge import KnowledgeStore
from nexus.transcription import TranscriptionError, transcribe_file
from nexus.web.github import GitHubRepositoryError, download_public_repository


READ_ONLY_TOOLS = {"list_directory", "read_file", "search_code", "recall"}


def _secret_or_env(name: str) -> str | None:
    try:
        value = st.secrets.get(name)
    except (FileNotFoundError, KeyError):
        value = None
    return value or os.getenv(name)


def _run(coroutine):
    return asyncio.run(coroutine)


async def _ask_agent(
    *,
    model: str,
    workspace: Path,
    database: Path,
    prompt: str,
    max_steps: int,
) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]]]:
    agent = Agent(
        model_name=model,
        db_path=str(database),
        session_id="streamlit-demo",
        max_steps=max_steps,
        cost_budget_usd=0.25,
        workspace_root=workspace,
        enabled_skill_names=READ_ONLY_TOOLS,
    )
    try:
        await agent.init()
        answer = await agent.chat(prompt)
        state = await agent.memory.load_state()
        history = await agent.memory.get_history()
        return answer, state.model_dump() if state else None, history
    finally:
        await agent.close()


def _show_state(state: dict[str, Any] | None) -> None:
    if not state:
        return
    columns = st.columns(4)
    columns[0].metric("Status", state["status"])
    columns[1].metric("Steps", f"{state['step_count']}/{state['max_steps']}")
    columns[2].metric("Tool calls", state["tool_calls"])
    columns[3].metric("Checkpoints", state["checkpoint_count"])
    with st.expander("Authoritative runtime state"):
        st.json(state)


def _show_history(history: list[dict[str, Any]]) -> None:
    with st.expander("Persisted protocol trace"):
        for message in history[-12:]:
            role = message.get("role", "unknown")
            if role == "tool":
                label = f"tool:{message.get('name', 'unknown')} ({message.get('tool_call_id', '?')})"
            else:
                label = role
            st.code(f"{label}\n{message.get('content') or message.get('tool_calls') or ''}")


def _show_knowledge(root: Path) -> None:
    store = KnowledgeStore(root / ".aetheris/wiki")
    pages = store.pages()
    with st.expander("Knowledge base / wiki", expanded=bool(pages)):
        st.caption("Human-readable durable facts. Use [[page-topic]] inside Markdown to create a link.")
        if not pages:
            st.info("No durable facts in this workspace yet. The local CLI can create them with the remember skill.")
            return
        st.graphviz_chart(store.graph_dot(), use_container_width=True)
        for page in pages:
            with st.expander(page.stem.replace("-", " ").title()):
                st.markdown(page.read_text(encoding="utf-8"))


st.set_page_config(page_title="Aetheris", page_icon="◈", layout="wide")
st.title("Aetheris")
st.caption("A small, resumable coding-agent runtime — deployed as a read-only GitHub exploration demo.")

if "repo_root" not in st.session_state:
    st.session_state.repo_root = None
if "repo_name" not in st.session_state:
    st.session_state.repo_name = None
if "repo_temp" not in st.session_state:
    st.session_state.repo_temp = None
if "messages" not in st.session_state:
    st.session_state.messages = []

with st.sidebar:
    st.header("Demo controls")
    model = st.text_input("Model", value=os.getenv("AETHERIS_MODEL", "openai/gpt-4o-mini"))
    max_steps = st.slider("Maximum steps", min_value=1, max_value=12, value=6)
    st.caption("The hosted demo exposes only read-only tools. Shell, write, edit, and push operations are intentionally unavailable.")

    st.subheader("Load a public repository")
    repository_url = st.text_input("GitHub URL", value="https://github.com/ashenclock/aetheris")
    branch = st.text_input("Branch (optional)", value="feature/long-horizon-agent")
    if st.button("Load repository", type="primary"):
        try:
            with st.spinner("Downloading a bounded public archive from GitHub..."):
                repository, root, temporary = download_public_repository(repository_url, branch)
            old_temp = st.session_state.repo_temp
            if old_temp is not None:
                old_temp.cleanup()
            st.session_state.repo_temp = temporary
            st.session_state.repo_root = str(root)
            st.session_state.repo_name = f"{repository.owner}/{repository.name}:{branch or 'default'}"
            st.session_state.messages = []
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

if st.session_state.get("offline_summary"):
    summary = st.session_state.offline_summary
    st.subheader("Offline runtime evidence")
    st.success(f"{summary['success_rate']:.0%} success across {len(summary['tasks'])} deterministic cases")
    st.json(summary)

if st.session_state.repo_root:
    root = Path(st.session_state.repo_root)
    database = root / ".aetheris" / "streamlit.sqlite3"
    database.parent.mkdir(parents=True, exist_ok=True)
    st.subheader(f"Repository: {st.session_state.repo_name}")
    st.info("This is a bounded read-only analysis. The repository is held in an isolated temporary workspace and is removed when the session is replaced.")
    _show_knowledge(root)
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    prompt = st.chat_input("Ask Aetheris to inspect the repository...")
    if prompt:
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        api_key = _secret_or_env("OPENAI_API_KEY")
        if api_key:
            os.environ.setdefault("OPENAI_API_KEY", api_key)
        with st.chat_message("assistant"):
            with st.spinner("Aetheris is inspecting the repository..."):
                try:
                    answer, state, history = _run(
                        _ask_agent(
                            model=model,
                            workspace=root,
                            database=database,
                            prompt=prompt,
                            max_steps=max_steps,
                        )
                    )
                    st.markdown(answer)
                    _show_state(state)
                    _show_history(history)
                    st.session_state.messages.append({"role": "assistant", "content": answer})
                except Exception as exc:
                    st.error(f"The model request failed: {exc}")
                    st.caption("Configure the provider key in Streamlit Secrets or the environment before using the live GitHub analysis.")
else:
    st.subheader("Start with the deterministic path")
    st.write("Load a public GitHub repository, then ask a bounded read-only question. The offline demo is available in the sidebar and requires no API key.")
    st.code("streamlit run streamlit_app.py", language="bash")
