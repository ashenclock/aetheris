import os
from pydantic import BaseModel, Field
from .base_skill import BaseSkill


class WriteFileSchema(BaseModel):
    filepath: str = Field(
        ..., description="Absolute or relative path to the file to create/overwrite"
    )
    content: str = Field(..., description="The full content to write to the file")


class WriteFileSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "Creates a new file or completely overwrites an existing one."

    @property
    def parameters_schema(self) -> type[BaseModel]:
        return WriteFileSchema

    @property
    def requires_confirmation(self) -> bool:
        return True

    async def execute(self, **kwargs) -> str:
        filepath = kwargs.get("filepath", "")
        content = kwargs.get("content", "")

        if not filepath:
            return "Error: filepath cannot be empty."

        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)

            return f"Successfully wrote {len(content)} characters to '{filepath}'."
        except Exception as e:
            return f"Error writing file: {str(e)}"
