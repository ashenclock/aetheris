import os
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
            if not os.path.exists(filepath):
                return f"Error: The file '{filepath}' does not exist."
            if not os.path.isfile(filepath):
                return f"Error: '{filepath}' is not a valid file."

            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            return content
        except Exception as e:
            return f"Error reading file '{filepath}': {str(e)}"
