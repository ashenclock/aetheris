from __future__ import annotations

import re
from pathlib import Path


class KnowledgeStore:
    """Human-readable long-term knowledge stored as Markdown files."""

    def __init__(self, root: str | Path = ".aetheris/wiki"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _slug(topic: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")
        return slug or "note"

    def remember(self, topic: str, content: str) -> Path:
        path = self.root / f"{self._slug(topic)}.md"
        path.write_text(f"# {topic.strip()}\n\n{content.strip()}\n", encoding="utf-8")
        return path

    def recall(self, query: str, limit: int = 5) -> list[tuple[Path, str]]:
        needle = query.lower().strip()
        matches: list[tuple[Path, str]] = []
        for path in sorted(self.root.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            if needle in path.stem.lower() or needle in text.lower():
                matches.append((path, text))
            if len(matches) >= limit:
                break
        return matches
