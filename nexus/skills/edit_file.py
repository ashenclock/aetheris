from pydantic import BaseModel, Field

from .base_skill import BaseSkill


class EditFileSchema(BaseModel):
    filepath: str = Field(..., description="Path to the file to edit")
    target_content: str = Field(
        ..., description="The exact existing string in the file to replace"
    )
    replacement_content: str = Field(
        ..., description="The new string to replace it with"
    )


class EditFileSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return "Replaces a specific block of text in an existing file. Use this for small edits instead of overwriting the whole file."

    @property
    def parameters_schema(self) -> type[BaseModel]:
        return EditFileSchema

    @property
    def requires_confirmation(self) -> bool:
        return True

    async def execute(self, **kwargs) -> str:
        raw_filepath = kwargs.get("filepath", "")
        target = kwargs.get("target_content", "")
        replacement = kwargs.get("replacement_content", "")

        filepath = self.resolve_path(raw_filepath)
        if not raw_filepath or not target:
            return "Error: filepath and target_content cannot be empty."
        if filepath is None:
            return f"Error: file '{raw_filepath}' is outside the workspace."

        try:
            if not filepath.exists():
                return f"Error: The file '{filepath}' does not exist."

            content = filepath.read_text(encoding="utf-8")

            if target not in content:
                return "Error: target_content not found exactly as provided in the file. Watch out for whitespace and indentation."

            # Replace only the first occurrence to avoid unintended changes.
            filepath.write_text(
                content.replace(target, replacement, 1), encoding="utf-8"
            )

            return f"Successfully edited '{filepath}'."
        except OSError as exc:
            return f"Error editing file: {exc}"
