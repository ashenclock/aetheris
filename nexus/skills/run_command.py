from __future__ import annotations

import asyncio
import os
import signal

from pydantic import BaseModel, Field

from .base_skill import BaseSkill


class RunCommandSchema(BaseModel):
    command: str = Field(..., description="The shell command to execute")
    timeout: int = Field(default=30, ge=1, le=600, description="Timeout in seconds")


class RunCommandSkill(BaseSkill):
    OUTPUT_LIMITS = {"stdout": 8_000, "stderr": 4_000}

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
        readers = []
        communication = None
        try:
            # Preserve failures inside pipelines instead of trusting the last filter.
            shell_options = {}
            if os.name != "nt":
                shell_options["executable"] = "/bin/bash"
                command = "set -o pipefail; " + command
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.workspace_root) if self.workspace_root else None,
                start_new_session=os.name != "nt",
                **shell_options,
            )
            readers = [
                asyncio.create_task(self._read_output(stream, limit))
                for stream, limit in zip(
                    (process.stdout, process.stderr), self.OUTPUT_LIMITS.values()
                )
            ]
            communication = asyncio.gather(*readers, process.wait())
            try:
                stdout, stderr, _ = await asyncio.wait_for(
                    asyncio.shield(communication), timeout=timeout
                )
            except asyncio.TimeoutError:
                await self._stop_process_tree(process)
                try:
                    await asyncio.wait_for(asyncio.shield(communication), timeout=1)
                except asyncio.TimeoutError:
                    communication.cancel()
                    await asyncio.gather(communication, return_exceptions=True)
                return f"Error: command timed out after {timeout} seconds."

            output, output_truncated = stdout
            errors, errors_truncated = stderr
            sections = [f"Exit code: {process.returncode}"]
            if output:
                suffix = "\n[stdout truncated]" if output_truncated else ""
                sections.append(f"STDOUT:\n{output[:2_000]}{suffix}")
            if errors:
                suffix = "\n[stderr truncated]" if errors_truncated else ""
                sections.append(f"STDERR:\n{errors[:1_000]}{suffix}")
            result = "\n\n".join(sections)
            if process.returncode != 0:
                return (
                    f"Error: command exited with code {process.returncode}.\n{result}"
                )
            return result
        except asyncio.CancelledError:
            if process is not None:
                await self._stop_process_tree(process)
                await asyncio.gather(*readers, return_exceptions=True)
            raise
        except Exception as exc:
            if process is not None:
                await self._stop_process_tree(process)
                await asyncio.gather(*readers, return_exceptions=True)
            return f"Error: command execution failed: {exc}"

    @staticmethod
    async def _read_output(stream, limit: int) -> tuple[str, bool]:
        captured = bytearray()
        truncated = False
        while chunk := await stream.read(64 * 1024):
            remaining = limit - len(captured)
            if remaining > 0:
                captured.extend(chunk[:remaining])
            if len(chunk) > remaining:
                truncated = True
        return captured.decode(errors="replace"), truncated

    @staticmethod
    async def _stop_process_tree(process) -> None:
        def signal_tree(sig: int) -> None:
            try:
                if os.name == "nt":
                    process.kill()
                else:
                    os.killpg(process.pid, sig)
            except ProcessLookupError:
                pass

        cancelled = False
        signal_tree(signal.SIGTERM)
        try:
            await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            cancelled = True
        finally:
            signal_tree(signal.SIGKILL)
        try:
            await asyncio.wait_for(process.wait(), timeout=1.0)
        except asyncio.CancelledError:
            cancelled = True
        except asyncio.TimeoutError:
            pass
        if cancelled:
            raise asyncio.CancelledError
