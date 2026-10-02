"""Readable activity events, shared by terminal and web views."""

from __future__ import annotations

import json
import os
import re
from typing import Any


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "<redacted>"
            if re.search(
                r"(?i)(?:api[_-]?key|password|secret|token|authorization)$",
                str(key),
            )
            else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if not isinstance(value, str):
        return value
    for key, secret in os.environ.items():
        if len(secret) >= 8 and any(
            word in key.upper() for word in ("KEY", "TOKEN", "SECRET", "PASSWORD")
        ):
            value = value.replace(secret, "<redacted>")
    value = re.sub(r"(?i)(bearer\s+)\S+", r"\1<redacted>", value)
    return re.sub(
        r"(?i)((?:api[_-]?key|token|password|secret)\s*[:=]\s*)[^\s,;]+",
        r"\1<redacted>",
        value,
    )


def format_event(event: str, details: dict[str, Any]) -> str:
    details = redact(details)
    name = details.get("name", "tool")
    if event == "model_start":
        return f"Waiting for {details['model']} (timeout: {details['timeout_seconds']:g}s)…"
    if event == "model_wait":
        return f"Still waiting for provider: {details['elapsed_seconds']}s elapsed."
    if event == "model_plan":
        return "Plan: " + str(details["text"])[:1000]
    if event == "model_error":
        return "Model request failed: " + str(details["error"])
    if event in {"tool_start", "approval_required"}:
        args = details.get("arguments")
        prefix = "Approval needed" if event == "approval_required" else "→"
        return f"{prefix} {name}" + (
            " " + json.dumps(args, ensure_ascii=False) if args else ""
        )
    if event == "tool_repeat":
        return f"↻ Repeated {name} blocked; previous observation reused."
    if event == "tool_output":
        return f"{name} result:\n{details['preview']}"
    if event in {"tool_done", "tool_failed"}:
        label = "✓" if event == "tool_done" else "⚠"
        outcome = "completed" if event == "tool_done" else "failed"
        preview = details.get("preview") or details.get("error")
        return f"{label} {name} {outcome}" + (
            "\n" + str(preview)[:1500] if preview else ""
        )
    return event
