from nexus.core.knowledge import KnowledgeStore


def test_markdown_knowledge_roundtrip(tmp_path):
    store = KnowledgeStore(tmp_path / "wiki")
    path = store.remember("Architecture decision", "Use SQLite for durable task state.")

    assert path.exists()
    matches = store.recall("SQLite")
    assert len(matches) == 1
    assert "durable task state" in matches[0][1]
