from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel

from .base_skill import BaseSkill

IGNORED_DIRS = {
    ".aetheris",
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}
PROJECT_FILES = (
    "README.md",
    "pyproject.toml",
    "requirements.txt",
    "package.json",
    "Dockerfile",
    "docker-compose.yml",
    "Makefile",
)


class WorkspaceOverviewSchema(BaseModel):
    pass


class WorkspaceOverviewSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "inspect_workspace"

    @property
    def description(self) -> str:
        return (
            "Inspect the workspace at a glance: report its root, common project "
            "entry files, and a bounded directory tree (maximum depth 2 and 100 "
            "entries). Does not open file contents or hidden/private files. Use "
            "this first when asked to explore or explain a repository."
        )

    @property
    def parameters_schema(self) -> type[BaseModel]:
        return WorkspaceOverviewSchema

    async def execute(self, **kwargs: object) -> str:
        del kwargs
        root = self.workspace_root or Path.cwd()
        if not root.is_dir():
            return f"Error: workspace root is not a directory: {root}"

        lines = [f"Workspace: {root}", "Project files:"]
        for name in PROJECT_FILES:
            if (root / name).is_file():
                lines.append(f"- {name}")

        lines.append("Directory tree (depth <= 2):")
        count = 0
        for current, dirs, files in os.walk(root, followlinks=False):
            current_path = Path(current)
            depth = len(current_path.relative_to(root).parts)
            dirs[:] = (
                sorted(
                    name
                    for name in dirs
                    if name not in IGNORED_DIRS
                    and not name.startswith(".")
                    and not (current_path / name).is_symlink()
                )
                if depth < 2
                else []
            )
            children = dirs + sorted(
                name
                for name in files
                if not name.startswith(".") and not (current_path / name).is_symlink()
            )
            for name in children:
                path = current_path / name
                relative = path.relative_to(root)
                lines.append(f"- {relative}{'/' if path.is_dir() else ''}")
                count += 1
                if count >= 100:
                    lines.append("- ... (entry limit reached)")
                    return "\n".join(lines)
        return "\n".join(lines)
