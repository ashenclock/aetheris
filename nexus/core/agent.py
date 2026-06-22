import os
import asyncio
import json
import logging
from litellm import acompletion
from rich.console import Console
import inspect
import importlib
import pkgutil

from .memory import SessionMemory
from .tracker import CostTracker
from nexus.skills.base_skill import BaseSkill
from nexus.skills import list_dir, read_file

logger = logging.getLogger("AetherisAgent")
console = Console()

class Agent:
    def __init__(self, model_name: str, db_path: str = "memory.db", session_id: str = "default"):
        self.model_name = model_name
        self.session_id = session_id
        # In memory.py the SessionMemory class expects session_id as the first param or kwargs, let's assume it accepts session_id.
        # Wait, let me check memory.py definition first... Ah I can't check easily without a tool, but I wrote it! 
        # I remember `SessionMemory` takes `session_id="default"` and `db_path="memory.db"`.
        self.memory = SessionMemory(session_id=self.session_id, db_path=db_path)
        self.tracker = CostTracker()
        self.skills = {}
        self._load_skills()
        
        # Load Mega Prompt
        prompt_path = os.path.join(os.path.dirname(__file__), "prompts", "mega_prompt.md")
        try:
            with open(prompt_path, "r", encoding="utf-8") as f:
                self.system_prompt = f.read()
        except FileNotFoundError:
            self.system_prompt = "Sei Aetheris, un Senior Full-Stack Engineer."

    def _load_skills(self):
        """
        Carica dinamicamente tutte le skill dalla cartella nexus/skills/
        """
        import importlib
        import pkgutil
        import inspect
        import nexus.skills
        from nexus.skills.base_skill import BaseSkill
        
        pkg_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "skills"))
        
        for importer, modname, ispkg in pkgutil.iter_modules([pkg_path]):
            if modname == "base_skill":
                continue
            try:
                module = importlib.import_module(f"nexus.skills.{modname}")
                for name, obj in inspect.getmembers(module):
                    if (inspect.isclass(obj) 
                        and issubclass(obj, BaseSkill) 
                        and obj is not BaseSkill):
                        skill_instance = obj()
                        self.skills[skill_instance.name] = skill_instance
                        logger.debug(f"Loaded skill: {skill_instance.name}")
            except Exception as e:
                logger.warning(f"Could not load skill module '{modname}': {e}")

    def _get_litellm_tools(self) -> list:
        tools = []
        for name, skill in self.skills.items():
            tools.append({
                "type": "function",
                "function": skill.get_function_schema()
            })
        return tools if tools else None

    async def init(self):
        await self.memory.init_db()
        history = await self.memory.get_history()
        if not history:
            await self.memory.add_message("system", self.system_prompt)

    async def chat(self, user_input: str):
        await self.memory.add_message("user", user_input)
        
        from rich.prompt import Confirm
        
        while True:
            messages = await self.memory.get_history()
            tools = self._get_litellm_tools()
            
            try:
                response = await acompletion(
                    model=self.model_name,
                    messages=messages,
                    tools=tools
                )
                
                self.tracker.add_usage(response)
                response_message = response.choices[0].message
                
                if response_message.tool_calls:
                    for tool_call in response_message.tool_calls:
                        func_name = tool_call.function.name
                        func_args = json.loads(tool_call.function.arguments)
                        
                        # Salva in memoria che l'assistente ha chiamato il tool
                        await self.memory.add_message("assistant", f"I am executing tool '{func_name}' with arguments: {func_args}")
                        
                        console.print(f"[dim yellow]⚡ Richiesta esecuzione tool: {func_name}({func_args})[/dim yellow]")
                        
                        if func_name in self.skills:
                            skill = self.skills[func_name]
                            
                            if skill.requires_confirmation:
                                console.print(f"[bold red]⚠️ L'agente vuole eseguire un'azione sensibile: {func_name}[/bold red]")
                                if not Confirm.ask("Autorizzi l'esecuzione?"):
                                    result = f"L'utente ha NEGATO l'esecuzione di '{func_name}'."
                                    await self.memory.add_message("user", f"Risultato di '{func_name}':\n{result}")
                                    continue
                                    
                            try:
                                result = await skill.execute(**func_args)
                            except Exception as e:
                                result = f"Error executing tool: {e}"
                        else:
                            result = f"Error: Tool '{func_name}' not found."
                            
                        # Usiamo "user" invece di "tool" per evitare che DeepSeek o OpenAI crashino richiedendo il tool_call_id
                        await self.memory.add_message("user", f"Tool '{func_name}' execution result:\n{result}")
                        
                    continue
                else:
                    reply = response_message.content
                    await self.memory.add_message("assistant", reply)
                    return reply
                    
            except Exception as e:
                logger.error(f"Error during LLM call: {e}")
                return f"Error: {str(e)}"
