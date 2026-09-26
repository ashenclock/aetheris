from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from .base_skill import BaseSkill


class RunCommandSchema(BaseModel):
    command: str = Field(..., description="The shell command to execute")
    timeout: int = Field(default=30, ge=1, le=600, description="Timeout in seconds")


class RunCommandSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "run_command"

    @property
    def description(self) -> str:
        return (
            "Run an approved shell command in the current operating-system environment."
        )

    @property
    def parameters_schema(self) -> type[BaseModel]:
        return RunCommandSchema

    @property
    def requires_confirmation(self) -> bool:
        return True

    async def execute(self, **kwargs) -> str:
        command = kwargs.get("command", "").strip()
        timeout = kwargs.get("timeout", 30)
        if not command:
            return "Error: command cannot be empty."

        process = None
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.communicate()
                return f"Error: command timed out after {timeout} seconds."

            output = stdout.decode(errors="replace") if stdout else ""
            errors = stderr.decode(errors="replace") if stderr else ""
            sections = [f"Exit code: {process.returncode}"]
            if output:
                sections.append(f"STDOUT:\n{output[:2_000]}")
            if errors:
                sections.append(f"STDERR:\n{errors[:1_000]}")
            result = "\n\n".join(sections)
            if process.returncode != 0:
                return (
                    f"Error: command exited with code {process.returncode}.\n{result}"
                )
            return result
        except Exception as exc:
            return f"Error: command execution failed: {exc}"
