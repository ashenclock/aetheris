"""Optional live provider smoke test.

Discovery is always offline. Network calls happen only with ``--live`` and
only for providers whose credentials are already present in the environment.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from typing import Any

from dotenv import load_dotenv

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
from litellm import acompletion

from nexus.providers import PROVIDERS, discover_providers

load_dotenv()


async def smoke(model: str) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        response = await acompletion(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": "Reply with exactly the word READY.",
                }
            ],
            max_tokens=8,
            temperature=0,
            timeout=30,
        )
        usage = getattr(response, "usage", None)
        return {
            "model": model,
            "status": "passed",
            "text": str(response.choices[0].message.content or "").strip(),
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }
    except Exception as exc:  # provider errors are the result of this smoke test
        return {
            "model": model,
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }


async def run_smokes(models: list[str]) -> list[dict[str, Any]]:
    return list(await asyncio.gather(*(smoke(model) for model in models)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        action="append",
        dest="models",
        help="Explicit LiteLLM model; repeat the option for multiple providers.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Make bounded live calls. Without this flag, only print the plan.",
    )
    args = parser.parse_args()
    discovered = discover_providers()
    configured_models = {
        str(item["model"])
        for item in discovered
        if item["configured"] and str(item["provider"]) != "Ollama"
    }
    models = args.models or [
        spec.model for spec in PROVIDERS if spec.model in configured_models
    ]
    if not models:
        print(
            json.dumps(
                {
                    "status": "skipped",
                    "reason": "No supported provider credentials detected.",
                    "hint": "Run `aetheris providers` or set a provider key in .env.",
                },
                indent=2,
            )
        )
        return 0
    if not args.live:
        print(json.dumps({"status": "plan", "models": models}, indent=2))
        return 0
    results = asyncio.run(run_smokes(models))
    print(json.dumps({"status": "completed", "results": results}, indent=2))
    return 0 if all(item["status"] == "passed" for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
