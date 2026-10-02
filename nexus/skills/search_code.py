import os
from pathlib import Path
from pydantic import BaseModel, Field, ValidationError
from .base_skill import BaseSkill

MAX_SEARCH_FILE_BYTES = 1_000_000
MAX_SEARCH_RESULTS = 200


class SearchCodeSchema(BaseModel):
    pattern: str = Field(
        ..., min_length=1, max_length=200, description="Literal text to search for"
    )
    extension: str = Field(
        "", max_length=16, description="File extension to filter by (e.g. .py, .md)"
    )
    max_results: int = Field(
        20,
        ge=1,
        le=MAX_SEARCH_RESULTS,
        description=f"Maximum matches to return (hard limit {MAX_SEARCH_RESULTS})",
    )


class SearchCodeSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "search_code"

    @property
    def description(self) -> str:
        return (
            "Searches for literal text in the workspace; skips hidden/generated and "
            f"symlinked files and files larger than {MAX_SEARCH_FILE_BYTES} bytes. "
            f"Returns at most {MAX_SEARCH_RESULTS} matches."
        )

    @property
    def parameters_schema(self) -> type[BaseModel]:
        return SearchCodeSchema

    async def execute(self, **kwargs) -> str:
        try:
            arguments = SearchCodeSchema.model_validate(kwargs)
        except ValidationError as exc:
            return f"Error: invalid search arguments: {exc}"

        pattern = arguments.pattern
        extension = arguments.extension
        max_results = arguments.max_results

        if not pattern:
            return "Error: pattern cannot be empty."

        results = []
        root = self.workspace_root or Path.cwd()
        ignored_dirs = {
            ".git",
            ".aetheris",
            ".pytest_cache",
            ".ruff_cache",
            ".venv",
            "__pycache__",
            "build",
            "dist",
            "node_modules",
            "venv",
        }
        needle = pattern.casefold()

        for directory, subdirectories, filenames in os.walk(root, followlinks=False):
            subdirectories[:] = [
                name
                for name in subdirectories
                if name not in ignored_dirs and not name.startswith(".")
            ]
            for filename in filenames:
                file_path = Path(directory) / filename
                if filename.startswith(".") or file_path.is_symlink():
                    continue
                if extension and file_path.suffix.lower() != extension.lower():
                    continue
                try:
                    if file_path.stat().st_size > MAX_SEARCH_FILE_BYTES:
                        continue
                    with file_path.open(
                        "r", encoding="utf-8", errors="ignore"
                    ) as source:
                        for line_number, line in enumerate(source, 1):
                            if needle in line.casefold():
                                results.append(
                                    f"{file_path}:{line_number}: {line.strip()}"
                                )
                                if len(results) >= max_results:
                                    break
                except OSError:
                    continue
                if len(results) >= max_results:
                    break
            if len(results) >= max_results:
                break

        if not results:
            return f"No results found for '{pattern}'."

        output = f"Found {len(results)} results for '{pattern}':\n\n"
        output += "\n".join(results)
        return output
