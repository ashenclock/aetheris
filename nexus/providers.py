from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class ProviderSpec:
    """A provider entry used for deterministic discovery and operator guidance."""

    name: str
    model: str
    env_vars: tuple[str, ...]
    notes: str

    def configured(self, environ: Mapping[str, str]) -> bool:
        return any(environ.get(variable, "").strip() for variable in self.env_vars)


PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        "OpenAI",
        "openai/gpt-4o-mini",
        ("OPENAI_API_KEY",),
        "LiteLLM chat completion",
    ),
    ProviderSpec(
        "DeepSeek",
        "deepseek/deepseek-chat",
        ("DEEPSEEK_API_KEY",),
        "LiteLLM chat completion",
    ),
    ProviderSpec(
        "Gemini",
        "gemini/gemini-2.5-flash",
        ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        "LiteLLM chat completion",
    ),
    ProviderSpec(
        "OpenRouter",
        "openrouter/openai/gpt-4o-mini",
        ("OPENROUTER_API_KEY",),
        "OpenAI-compatible multi-model gateway",
    ),
    ProviderSpec(
        "Anthropic",
        "anthropic/claude-3-5-haiku-latest",
        ("ANTHROPIC_API_KEY",),
        "LiteLLM chat completion",
    ),
)


def discover_providers(
    environ: Mapping[str, str] | None = None,
) -> list[dict[str, str | bool]]:
    """Report configured providers without making a network request or API call."""
    environment = os.environ if environ is None else environ
    discovered = [
        {
            "provider": spec.name,
            "model": spec.model,
            "configured": spec.configured(environment),
            "credentials": ", ".join(spec.env_vars),
            "notes": spec.notes,
        }
        for spec in PROVIDERS
    ]
    ollama_installed = shutil.which("ollama") is not None
    discovered.append(
        {
            "provider": "Ollama",
            "model": "ollama/llama3",
            "configured": ollama_installed,
            "credentials": "local Ollama server",
            "notes": "Local LiteLLM backend; model must be pulled separately",
        }
    )
    return discovered


def provider_for_model(model: str) -> str:
    """Return the provider family represented by a LiteLLM-style model name."""
    prefix = model.split("/", maxsplit=1)[0].lower()
    aliases = {
        "responses": "OpenAI Responses",
        "openai": "OpenAI",
        "deepseek": "DeepSeek",
        "gemini": "Gemini",
        "google": "Gemini",
        "openrouter": "OpenRouter",
        "anthropic": "Anthropic",
        "ollama": "Ollama",
        "mock": "Offline mock",
    }
    return aliases.get(prefix, prefix or "Unknown")
