from pathlib import Path

from pydantic import BaseModel, Field

from .base_skill import BaseSkill


class ReadFileSchema(BaseModel):
    filepath: str = Field(
        ..., description="The absolute or relative path to the file you want to read."
    )


class ReadFileSkill(BaseSkill):
    _SENSITIVE_NAMES = {".env", "credentials", "id_rsa", "id_ed25519"}
    _SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}
    _SENSITIVE_DIRECTORIES = {".aws", ".ssh", ".git"}

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "Reads the contents of a local file."

    @property
    def parameters_schema(self) -> type[BaseModel]:
        return ReadFileSchema

    @classmethod
    def _is_sensitive(cls, filepath: Path) -> bool:
        if filepath.name in cls._SENSITIVE_NAMES:
            return True
        if filepath.name.startswith(".env.") and filepath.name != ".env.example":
            return True
        if filepath.suffix.lower() in cls._SENSITIVE_SUFFIXES:
            return True
        return bool(cls._SENSITIVE_DIRECTORIES.intersection(filepath.parts))

    async def confirm(self, arguments: dict) -> bool:
        raw_filepath = arguments.get("filepath", "")
        filepath = self.resolve_path(raw_filepath)
        if filepath is None or not self._is_sensitive(filepath):
            return filepath is not None
        try:
            from rich.prompt import Confirm

            return Confirm.ask(
                f"Approve reading potentially sensitive file '{filepath}'?"
            )
        except (EOFError, KeyboardInterrupt):
            return False

    async def execute(self, **kwargs) -> str:
        raw_filepath = kwargs.get("filepath", "")
        filepath = self.resolve_path(raw_filepath)
        if not raw_filepath:
            return "Error: filepath cannot be empty."
        if filepath is None:
            return f"Error: file '{raw_filepath}' is outside the workspace."

        try:
            if not filepath.exists():
                return f"Error: The file '{filepath}' does not exist."
            if not filepath.is_file():
                return f"Error: '{filepath}' is not a valid file."
            return filepath.read_text(encoding="utf-8")
        except OSError as exc:
            return f"Error reading file '{filepath}': {exc}"
