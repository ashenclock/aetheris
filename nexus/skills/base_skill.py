from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel


class BaseSkill(ABC):
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

    async def confirm(self, arguments: dict[str, Any]) -> bool:
        if not self.requires_confirmation:
            return True
        try:
            from rich.prompt import Confirm
            return Confirm.ask(f"Approve {self.name}({arguments})?")
        except (EOFError, KeyboardInterrupt):
            return False

    @abstractmethod
    async def execute(self, **kwargs: Any) -> str: ...

    def get_function_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters_schema.model_json_schema(),
        }
