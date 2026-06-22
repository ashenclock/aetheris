import asyncio
import os
import re
import glob
import typer
import logging
import litellm
import time
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.live import Live
from rich.markdown import Markdown
from rich.theme import Theme
from dotenv import load_dotenv

# Suppress annoying LiteLLM logs
litellm.suppress_debug_info = True
logging.getLogger("LiteLLM").setLevel(logging.WARNING)

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings

load_dotenv()

from nexus.core.agent import Agent

app = typer.Typer(name="Aetheris", help="A lightweight, modular Agentic AI CLI optimized for local inference.")

# Default Theme
custom_theme = Theme({
    "info": "dim cyan",
    "warning": "magenta",
    "danger": "bold red"
})
console = Console(theme=custom_theme)

LOGO_LINES = [
    r"    ___       __  __               _     ",
    r"   /   | ___ / /_/ /_  ___  ____  (_)____",
    r"  / /| |/ _ \ __/ __ \/ _ \/ __ \/ / ___/",
    r" / ___ /  __/ /_/ / / /  __/ / / / (__  )",
    r"/_/  |_\___/\__/_/ /_/\___/_/ /_/_/____/ "
]

class AetherisCompleter(Completer):
    """
    Real-time autocomplete for commands (/) and files (@).
    """
    def __init__(self):
        self.commands = {
            '/exit': 'Chiude la sessione in corso',
            '/quit': 'Chiude la sessione in corso',
            '/help': 'Mostra i comandi disponibili',
            '/clear': 'Pulisce lo schermo del terminale',
            '/cd': 'Cambia cartella (es. /cd ..)',
            '/ls': 'Mostra i file nella cartella corrente',
            '/new': 'Crea una nuova sessione isolata (es. /new projectX)',
            '/resume': 'Riprende una sessione esistente (es. /resume projectX)',
            '/agent': 'Crea un agente personalizzato (es. /agent Tester "Sei un QA eng...")',
            '/mcp': 'Elenca i server MCP disponibili',
            '/theme': 'Cambia il tema visivo (es. /theme hacker)'
        }
        
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        word_before_cursor = document.get_word_before_cursor(WORD=True)

        if word_before_cursor.startswith('/'):
            for cmd, desc in self.commands.items():
                if cmd.startswith(word_before_cursor):
                    yield Completion(cmd, start_position=-len(word_before_cursor), display_meta=desc)
        
        elif text.strip().startswith('/cd '):
            search_path = text.split('/cd ', 1)[1]
            if not search_path:
                pattern = "*/"
            elif search_path.endswith('/'):
                pattern = search_path + "*/"
            else:
                pattern = search_path + "*"
                
            for file in glob.glob(pattern):
                if os.path.isdir(file):
                    display = file + "/"
                    yield Completion(display, start_position=-len(search_path), display_meta="Directory")
                    
        elif word_before_cursor.startswith('@'):
            search_path = word_before_cursor[1:]
            
            if not search_path:
                pattern = "*"
            elif search_path.endswith('/'):
                pattern = search_path + "*"
            else:
                pattern = search_path + "*"
                
            files = glob.glob(pattern)
            
            for file in files:
                is_dir = os.path.isdir(file)
                display = file + ("/" if is_dir else "")
                completion_text = "@" + display
                desc = "Cartella" if is_dir else "File"
                yield Completion(completion_text, start_position=-len(word_before_cursor), display_meta=desc)

def process_file_tags(user_in: str) -> str:
    matches = re.findall(r'@([^\s]+)', user_in)
    
    if not matches:
        return user_in
        
    appended_content = "\n\n--- LOCAL FILES CONTEXT ---\n"
    files_found = 0
    
    for filepath in matches:
        if os.path.exists(filepath) and os.path.isfile(filepath):
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                appended_content += f"\nFile: {filepath}\n```\n{content}\n```\n"
                files_found += 1
            except Exception as e:
                console.print(f"[bold yellow]Warning: Could not read {filepath} - {e}[/bold yellow]")
        elif os.path.isdir(filepath):
            console.print(f"[bold yellow]Warning: '{filepath}' is a directory, not a file.[/bold yellow]")
        else:
            console.print(f"[bold red]Warning: File '{filepath}' not found.[/bold red]")
            
    if files_found > 0:
        return user_in + appended_content
    return user_in

async def animate_logo():
    for i in range(1, len(LOGO_LINES) + 1):
        os.system('cls' if os.name == 'nt' else 'clear')
        partial_logo = "\n".join(LOGO_LINES[:i])
        console.print(Text(partial_logo, style="bold cyan"))
        await asyncio.sleep(0.08)
    print()

def apply_theme(theme_name: str):
    global console
    if theme_name == "hacker":
        t = Theme({"info": "bold green", "warning": "yellow", "danger": "red"})
        console = Console(theme=t)
        return "bold green"
    elif theme_name == "dracula":
        t = Theme({"info": "magenta", "warning": "yellow", "danger": "red"})
        console = Console(theme=t)
        return "magenta"
    else:
        # Default
        t = Theme({"info": "dim cyan", "warning": "magenta", "danger": "bold red"})
        console = Console(theme=t)
        return "bold cyan"

@app.command()
def chat(
    model: str = typer.Option("ollama/llama3", "--model", "-m", help="LiteLLM provider/model string"),
    db_path: str = typer.Option("aetheris_memory.db", "--db", "-d", help="Path to SQLite memory DB")
):
    asyncio.run(animate_logo())
    
    console.print(Panel(f"[bold cyan]Aetheris Agent Initialized[/bold cyan]\nModel: [yellow]{model}[/yellow]\nMemory: [yellow]{db_path}[/yellow]", title="Welcome"))
    
    current_session_id = "default"
    agent = Agent(model_name=model, db_path=db_path, session_id=current_session_id)
    
    bindings = KeyBindings()
    
    @bindings.add('c-l')
    def _(event):
        os.system('cls' if os.name == 'nt' else 'clear')
        console.print(Text("\n".join(LOGO_LINES), style="bold cyan"))
        
    session = PromptSession(completer=AetherisCompleter(), key_bindings=bindings)
    
    async def run_chat():
        nonlocal agent, current_session_id
        await agent.init()
        prompt_color = "cyan"
        
        while True:
            try:
                cwd = os.path.basename(os.getcwd())
                stats = agent.tracker.summary()
                prompt_toks = stats.get("latest_prompt_tokens", 0)
                comp_toks = stats.get("latest_completion_tokens", 0)
                
                # Dinamic Prompt with Session ID
                prompt_text = HTML(f"<b><{prompt_color}>[{current_session_id} - {cwd}]</{prompt_color}> <green>(In: {prompt_toks} | Out: {comp_toks})</green> &gt;</b> ")
                
                user_in = await session.prompt_async(prompt_text)
                user_in = user_in.strip()
                
                if not user_in:
                    continue
                    
                if user_in.startswith("/"):
                    cmd_parts = user_in.split()
                    cmd = cmd_parts[0].lower()
                    
                    if cmd in ["/exit", "/quit"]:
                        console.print("[info]Aetheris:[/info] Arrivederci! 👋")
                        break
                    elif cmd == "/help":
                        console.print("[info]Comandi Locali:[/info]")
                        for c, desc in AetherisCompleter().commands.items():
                            console.print(f"  [yellow]{c}[/yellow] - {desc}")
                        continue
                    elif cmd == "/clear":
                        os.system('cls' if os.name == 'nt' else 'clear')
                        console.print(Text("\n".join(LOGO_LINES), style="bold cyan"))
                        continue
                    elif cmd == "/cd":
                        if len(cmd_parts) > 1:
                            target_dir = os.path.expanduser(" ".join(cmd_parts[1:]))
                            try:
                                os.chdir(target_dir)
                            except Exception as e:
                                console.print(f"[danger]Errore:[/danger] {e}")
                        continue
                    elif cmd == "/ls":
                        for f in os.listdir('.'):
                            color = "blue" if os.path.isdir(f) else "white"
                            console.print(f"[{color}]{f}[/{color}]")
                        continue
                    elif cmd == "/new":
                        if len(cmd_parts) > 1:
                            current_session_id = cmd_parts[1]
                            agent = Agent(model_name=model, db_path=db_path, session_id=current_session_id)
                            await agent.init()
                            console.print(f"[info]Iniziata nuova sessione:[/info] {current_session_id}")
                        else:
                            console.print("[warning]Uso: /new <nome_sessione>[/warning]")
                        continue
                    elif cmd == "/resume":
                        if len(cmd_parts) > 1:
                            current_session_id = cmd_parts[1]
                            agent = Agent(model_name=model, db_path=db_path, session_id=current_session_id)
                            await agent.init()
                            console.print(f"[info]Ripresa sessione:[/info] {current_session_id}")
                        else:
                            console.print("[warning]Uso: /resume <nome_sessione>[/warning]")
                        continue
                    elif cmd == "/agent":
                        if len(cmd_parts) > 2:
                            agent_name = cmd_parts[1]
                            custom_prompt = " ".join(cmd_parts[2:])
                            current_session_id = f"agent_{agent_name}"
                            agent = Agent(model_name=model, db_path=db_path, session_id=current_session_id)
                            # Override prompt before init so it's injected as the first system message
                            agent.system_prompt = custom_prompt
                            await agent.init()
                            console.print(f"[info]Nuovo Agente creato ({agent_name}) su sessione {current_session_id}[/info]")
                        else:
                            console.print("[warning]Uso: /agent <nome> <prompt...>[/warning]")
                        continue
                    elif cmd == "/theme":
                        if len(cmd_parts) > 1:
                            prompt_color = apply_theme(cmd_parts[1])
                            console.print(f"[info]Tema aggiornato a {cmd_parts[1]}[/info]")
                        else:
                            console.print("[warning]Usi validi: /theme hacker | /theme dracula | /theme default[/warning]")
                        continue
                    elif cmd == "/mcp":
                        # Placeholder for future MCP implementation
                        console.print(Panel("[dim]Nessun server MCP collegato.[/dim]\n[italic]La funzionalità MCP sarà abilitata nella prossima release.[/italic]", title="🔌 Connessioni MCP"))
                        continue
                    else:
                        console.print(f"[danger]Comando locale non riconosciuto: {cmd}[/danger]")
                        continue
                
                processed_input = process_file_tags(user_in)
                
                console.print(f"[dim]Thinking with {model}...[/dim]")
                reply = await agent.chat(processed_input)
                
                # Markdown rendering support
                console.print(f"\n[{prompt_color} bold]Aetheris:[/]")
                console.print(Markdown(reply))
                print() # extra spacing
                
            except KeyboardInterrupt:
                console.print("\n[info]Aetheris:[/info] Chiusura forzata. Arrivederci! 👋")
                break
            except EOFError:
                break
        
        summary = agent.tracker.summary()
        console.print(f"\n[dim]Session ended. Cost: ${summary['total_cost_usd']:.4f}[/dim]")

    asyncio.run(run_chat())

@app.command()
def run(
    task: str = typer.Argument(..., help="The task for the agent to execute autonomously"),
    model: str = typer.Option("ollama/llama3", "--model", "-m", help="LiteLLM provider/model string")
):
    console.print(f"[bold cyan]Task received:[/bold cyan] {task}")
    agent = Agent(model_name=model)
    
    async def run_task():
        await agent.init()
        processed_input = process_file_tags(task)
        reply = await agent.chat(processed_input)
        console.print(Panel(Markdown(reply), title="Aetheris Result", border_style="green"))

    asyncio.run(run_task())

if __name__ == "__main__":
    app()
