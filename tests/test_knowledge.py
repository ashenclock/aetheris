import pytest

from nexus.core.knowledge import KnowledgeStore


def test_markdown_knowledge_roundtrip(tmp_path):
    store = KnowledgeStore(tmp_path / "wiki")
    path = store.remember("Architecture decision", "Use SQLite for durable task state.")
    store.remember("Testing", "Run unit tests with pytest before release.")

    assert path.exists()
    matches = store.recall("SQLite")
    assert len(matches) == 1
    assert "durable task state" in matches[0][1]
    context = store.context("SQLite resumable state")
    assert "Architecture decision" in context


def test_knowledge_store_renders_linked_pages(tmp_path):
    store = KnowledgeStore(tmp_path / "wiki")
    store.remember(
        "Architecture", "Use [[testing]] for regression checks.", "README.md"
    )
    store.remember("Testing", "Run pytest.")

    graph = store.graph_dot()
    assert '"architecture" -> "testing"' in graph
    assert "Source: `README.md`" in (tmp_path / "wiki" / "architecture.md").read_text()


def test_knowledge_root_cannot_escape_workspace_through_symlink(tmp_path):
    workspace = tmp_path / "workspace"
    external = tmp_path / "private"
    workspace.mkdir()
    external.mkdir()
    (workspace / ".aetheris").mkdir()
    (workspace / ".aetheris/wiki").symlink_to(external, target_is_directory=True)

    with pytest.raises(ValueError, match="inside the workspace"):
        KnowledgeStore(workspace / ".aetheris/wiki", workspace)


def test_knowledge_pages_ignore_symlinked_files_and_refuse_overwrite(tmp_path):
    workspace = tmp_path / "workspace"
    wiki = workspace / ".aetheris/wiki"
    wiki.mkdir(parents=True)
    secret = tmp_path / "private.md"
    secret.write_text("private-marker-long-term", encoding="utf-8")
    (wiki / "private-marker.md").symlink_to(secret)
    store = KnowledgeStore(wiki, workspace)

    assert store.pages() == []
    assert store.recall("private-marker-long-term") == []
    with pytest.raises(ValueError, match="symbolic links"):
        store.remember("Private Marker", "must not overwrite")
    assert "private-marker-long-term" in secret.read_text(encoding="utf-8")
