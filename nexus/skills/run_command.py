import asyncio
from pydantic import BaseModel, Field
from .base_skill import BaseSkill

class RunCommandSchema(BaseModel):
    command: str = Field(..., description="The shell command to execute")
    timeout: int = Field(30, description="Timeout in seconds")

class RunCommandSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "run_command"

    @property
    def description(self) -> str:
        return "Executes a shell command and returns the output."

    @property
    def parameters_schema(self) -> type[BaseModel]:
        return RunCommandSchema
        
    @property
    def requires_confirmation(self) -> bool:
        return True

    async def execute(self, **kwargs) -> str:
        command = kwargs.get("command", "")
        timeout = kwargs.get("timeout", 30)
        
        if not command:
            return "Error: command cannot be empty."
            
        blocked_commands = ["rm -rf /", "mkfs", "dd if=/dev/zero"]
        for blocked in blocked_commands:
            if blocked in command:
                return f"ERROR: Command blocked for safety: '{blocked}'"
                
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
                
                output = stdout.decode() if stdout else ""
                errors = stderr.decode() if stderr else ""
                
                result = f"Exit code: {proc.returncode}\n"
                if output:
                    result += f"\nSTDOUT:\n{output[:2000]}"
                if errors:
                    result += f"\nSTDERR:\n{errors[:1000]}"
                return result
            except asyncio.TimeoutError:
                proc.kill()
                return f"Error: Command timed out after {timeout} seconds."
        except Exception as e:
            return f"Error executing command: {str(e)}"
