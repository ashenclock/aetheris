from pydantic import BaseModel, Field

from .base_skill import BaseSkill


class ReadFileSchema(BaseModel):
    filepath: str = Field(
        ..., description="The absolute or relative path to the file you want to read."
    )


class ReadFileSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "Reads the contents of a local file."

    @property
    def parameters_schema(self) -> type[BaseModel]:
        return ReadFileSchema

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
