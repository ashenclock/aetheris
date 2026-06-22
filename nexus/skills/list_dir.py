import os
import glob
from pydantic import BaseModel, Field
from .base_skill import BaseSkill

class ListDirSchema(BaseModel):
    path: str = Field(..., description="The path to the directory you want to list. Defaults to '.' if empty.")

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
        path = kwargs.get("path", ".")
        if not path:
            path = "."
            
        try:
            if not os.path.exists(path):
                return f"Error: The path '{path}' does not exist."
            if not os.path.isdir(path):
                return f"Error: '{path}' is a file, not a directory."
                
            entries = os.listdir(path)
            if not entries:
                return f"The directory '{path}' is empty."
                
            result = f"Contents of '{path}':\n"
            for entry in entries:
                full_path = os.path.join(path, entry)
                if os.path.isdir(full_path):
                    result += f"- {entry}/\n"
                else:
                    result += f"- {entry}\n"
            return result
        except Exception as e:
            return f"Error reading directory '{path}': {str(e)}"
