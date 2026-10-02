from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class BaseSkill(ABC):
    def __init__(self, workspace_root: str | Path | None = None) -> None:
        self.workspace_root = (
            Path(workspace_root).expanduser().resolve()
            if workspace_root is not None
            else None
        )

    def resolve_path(self, raw_path: str, *, must_exist: bool = False) -> Path | None:
        """Resolve a path and keep web/deployment workspaces inside their root."""
        if not raw_path:
            return None
        candidate = Path(raw_path).expanduser()
        if self.workspace_root is None:
            resolved = candidate.resolve()
        else:
            resolved = (self.workspace_root / candidate).resolve()
            try:
                resolved.relative_to(self.workspace_root)
            except ValueError:
                return None
        if must_exist and not resolved.exists():
            return None
        return resolved

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def parameters_schema(self) -> type[BaseModel]: ...

    @property
    def requires_confirmation(self) -> bool:
        return False

    @staticmethod
    def _ask_confirmation(prompt: str) -> bool:
        if not sys.stdin.isatty():
            return False
        try:
            from rich.prompt import Confirm
            from rich.text import Text

            return Confirm.ask(Text(prompt))
        except (EOFError, KeyboardInterrupt):
            return False

    async def confirm(self, arguments: dict[str, Any]) -> bool:
        if not self.requires_confirmation:
            return True
        return self._ask_confirmation(f"Approve {self.name}({arguments})?")

    @abstractmethod
    async def execute(self, **kwargs: Any) -> str: ...

    def get_function_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters_schema.model_json_schema(),
        }
