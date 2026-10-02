from pydantic import BaseModel, Field
from .base_skill import BaseSkill


class ListDirSchema(BaseModel):
    path: str = Field(
        ...,
        description="The path to the directory you want to list. Defaults to '.' if empty.",
    )


class ListDirSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "list_directory"

    @property
    def description(self) -> str:
        return "Lists the contents of a directory. Returns a list of files and subdirectories."

    @property
    def parameters_schema(self) -> type[BaseModel]:
        return ListDirSchema

    async def execute(self, **kwargs) -> str:
        raw_path = kwargs.get("path", ".") or "."
        path = self.resolve_path(raw_path)
        if path is None:
            return f"Error: path '{raw_path}' is outside the workspace."

        try:
            if not path.exists():
                return f"Error: The path '{path}' does not exist."
            if not path.is_dir():
                return f"Error: '{path}' is a file, not a directory."

            entries = list(path.iterdir())
            if not entries:
                return f"The directory '{path}' is empty."

            lines = [
                f"- {entry.name}{'/' if entry.is_dir() else ''}" for entry in entries
            ]
            return f"Contents of '{path}':\n" + "\n".join(lines) + "\n"
        except Exception as e:
            return f"Error reading directory '{path}': {str(e)}"
