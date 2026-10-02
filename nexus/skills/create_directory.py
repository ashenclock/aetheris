from pydantic import BaseModel, Field

from .base_skill import BaseSkill


class CreateDirectorySchema(BaseModel):
    path: str = Field(
        ..., min_length=1, description="Directory path inside the workspace"
    )


class CreateDirectorySkill(BaseSkill):
    name = "create_directory"
    description = "Create a directory and missing parents inside the workspace. Existing directories are unchanged."
    parameters_schema = CreateDirectorySchema
    requires_confirmation = True

    async def execute(self, **kwargs) -> str:
        directory = self.resolve_path(kwargs.get("path", ""))
        if directory is None:
            return "Error: directory path is empty or outside the workspace."
        try:
            directory.mkdir(parents=True, exist_ok=True)
            return f"Directory ready: {directory}"
        except OSError as exc:
            return f"Error creating directory: {exc}"
