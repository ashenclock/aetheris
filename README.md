<div align="center">

<pre>
    ___       __  __               _     
   /   | ___ / /_/ /_  ___  ____  (_)____
  / /| |/ _ \ __/ __ \/ _ \/ __ \/ / ___/
 / ___ /  __/ /_/ / / /  __/ / / / (__  )
/_/  |_\___/\__/_/ /_/\___/_/ /_/_/____/ 
</pre>

**A lightweight, local-first Auto-Agent CLI.**

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org)
[![LiteLLM](https://img.shields.io/badge/Powered_by-LiteLLM-orange.svg)](https://github.com/BerriAI/litellm)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

</div>

## 🌌 Overview

Aetheris is a modular, high-performance CLI Agent engineered for local development. Built on a pure ReAct loop architecture with `LiteLLM`, it functions as a fully autonomous Pair Programmer directly inside your terminal. It natively supports code writing, safe shell execution, and intelligent codebase navigation.

## ✨ Features

- **🧠 Provider Agnostic:** Thanks to LiteLLM, you can use local models (Ollama/Llama3, DeepSeek) or remote ones (OpenAI, Anthropic) seamlessly.
- **⚡ Auto-Agent Skills:** Aetheris can read files, write code, edit specific lines, list directories, and execute shell commands safely.
- **💾 Persistent SQLite Memory:** Every conversation is saved locally. Sessions are automatically compacted (summarized by the LLM) to save context tokens.
- **🔄 Multi-Session Support:** Switch between projects instantly using `/new <session_id>` or `/resume <session_id>`.
- **🎨 Rich Terminal UI:** Markdown rendering with syntax highlighting, autocomplete suggestions, and visual themes.

## 🚀 Quick Start

### Prerequisites
- Python 3.10+
- [Poetry](https://python-poetry.org/) (for dependency management)
- [Ollama](https://ollama.com/) (if using local models, default is `ollama/llama3`)

### Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/aetheris.git
cd aetheris
```

2. Install dependencies via Poetry:
```bash
poetry install
```

3. Launch Aetheris:
```bash
poetry run python -m nexus.cli chat --model ollama/llama3
```

## 🛠️ Usage

### Interactive CLI Commands
While inside the chat, you can use the following commands:
- `/new <name>`: Create a fresh, isolated session context.
- `/resume <name>`: Load a previous session context from the SQLite database.
- `/agent <name> <prompt>`: Create a temporary agent overriding the default personality.
- `/theme <name>`: Switch UI themes (`hacker`, `dracula`, `default`).
- `/clear`: Clear the terminal screen (or press `Ctrl+L`).
- `/help`: Show all available commands.

### Prompt Tagging
You can tag local files directly in your prompt using `@`:
```text
[default - aetheris] > Please explain the logic inside @nexus/core/agent.py
```

## 🧩 Architecture

Aetheris is built around a lightweight `Agent` class that orchestrates:
1. **Memory (`memory.py`)**: Asynchronous SQLite tracking of all messages.
2. **Skills (`skills/`)**: Dynamically loaded Python modules that grant the agent tool-calling abilities (e.g., `RunCommandSkill`, `WriteFileSkill`).
3. **Tracker (`tracker.py`)**: Real-time token and cost estimation.

## 🤝 Contributing

Contributions are what make the open source community such an amazing place to learn, inspire, and create. Any contributions you make are **greatly appreciated**.

Please refer to the [CONTRIBUTING.md](CONTRIBUTING.md) for detailed guidelines.

## 📄 License

Distributed under the MIT License. See `LICENSE` for more information.
