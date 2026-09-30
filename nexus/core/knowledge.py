from __future__ import annotations

import re
from collections import Counter
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

    def remember(self, topic: str, content: str, source: str | None = None) -> Path:
        path = self.root / f"{self._slug(topic)}.md"
        source_line = f"\n\nSource: `{source.strip()}`" if source and source.strip() else ""
        path.write_text(
            f"# {topic.strip()}\n\n{content.strip()}{source_line}\n",
            encoding="utf-8",
        )
        return path

    def pages(self) -> list[Path]:
        return sorted(self.root.glob("*.md"))

    def graph_dot(self) -> str:
        """Render lightweight Obsidian-style ``[[page]]`` links as Graphviz."""
        pages = {path.stem: path for path in self.pages()}
        edges: set[tuple[str, str]] = set()
        for page in pages.values():
            content = page.read_text(encoding="utf-8")
            for target in re.findall(r"\[\[([^\]]+)\]\]", content):
                target_slug = self._slug(target)
                if target_slug in pages:
                    edges.add((page.stem, target_slug))

        lines = [
            "digraph Knowledge {",
            '  rankdir=LR;',
            '  graph [bgcolor="transparent", pad=0.2];',
            '  node [shape=box, style="rounded,filled", fillcolor="#eef5f9", color="#24445c"];',
        ]
        for name in sorted(pages):
            safe_name = name.replace('"', "'")
            lines.append(f'  "{safe_name}" [label="{safe_name}"];')
        for source, target in sorted(edges):
            lines.append(f'  "{source}" -> "{target}";')
        lines.append("}")
        return "\n".join(lines)

    def recall(self, query: str, limit: int = 5) -> list[tuple[Path, str]]:
        terms = {
            term
            for term in re.findall(r"[a-z0-9_]{2,}", query.lower())
            if term not in {"the", "and", "for", "with", "from", "this", "that"}
        }
        matches: list[tuple[int, Path, str]] = []
        for path in self.pages():
            content = path.read_text(encoding="utf-8")
            words = Counter(
                re.findall(r"[a-z0-9_]{2,}", f"{path.stem} {content}".lower())
            )
            score = sum(words[term] for term in terms)
            if score:
                matches.append((score, path, content))
        matches.sort(key=lambda item: (-item[0], item[1].name))
        return [(path, content) for _, path, content in matches[:limit]]

    def context(self, query: str, limit: int = 3, char_limit: int = 1_200) -> str:
        matches = self.recall(query, limit=limit)
        if not matches:
            return ""
        pages = []
        for path, content in matches:
            excerpt = content.strip()
            if len(excerpt) > char_limit:
                excerpt = (
                    excerpt[: char_limit - 24].rstrip() + "\n[page excerpt omitted]"
                )
            pages.append(f"- {path.stem}: {excerpt}")
        return "\n".join(pages)
