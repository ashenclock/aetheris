import os
import re
from pathlib import Path
from pydantic import BaseModel, Field
from .base_skill import BaseSkill

class SearchCodeSchema(BaseModel):
    pattern: str = Field(..., description="Regex pattern or text to search for")
    extension: str = Field("", description="File extension to filter by (e.g. .py, .md)")
    max_results: int = Field(20, description="Max number of results to return")

class SearchCodeSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "search_code"

    @property
    def description(self) -> str:
        return "Searches for text or regex patterns across the local codebase."

    @property
    def parameters_schema(self) -> type[BaseModel]:
        return SearchCodeSchema

    async def execute(self, **kwargs) -> str:
        pattern = kwargs.get("pattern", "")
        extension = kwargs.get("extension", "")
        max_results = kwargs.get("max_results", 20)
        
        if not pattern:
            return "Error: pattern cannot be empty."
            
        results = []
        root = Path(".")
        
        for file_path in root.rglob("*"):
            if file_path.is_dir():
                continue
            if file_path.name.startswith(".") or "__pycache__" in str(file_path):
                continue
            if extension and not str(file_path).endswith(extension):
                continue
                
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    for i, line in enumerate(f, 1):
                        if re.search(pattern, line, re.IGNORECASE):
                            results.append(f"{file_path}:{i}: {line.strip()}")
                            if len(results) >= max_results:
                                break
            except Exception:
                continue
                
            if len(results) >= max_results:
                break
                
        if not results:
            return f"No results found for '{pattern}'."
            
        output = f"Found {len(results)} results for '{pattern}':\n\n"
        output += "\n".join(results)
        return output
