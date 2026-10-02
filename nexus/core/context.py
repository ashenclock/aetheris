"""Deterministic context selection; complete tool exchanges stay together."""

import json
from typing import Any


def bounded_history(
    history: list[dict[str, Any]],
    max_messages: int = 40,
    max_chars: int = 24_000,
    system_suffix: str = "",
) -> list[dict[str, Any]]:
    if max_chars < 128 or max_messages < 1:
        raise ValueError(
            "Context limits must allow at least 128 chars and one message."
        )

    has_system = bool(history and history[0].get("role") == "system")
    system_base = str(history[0].get("content") or "") if has_system else ""
    recent = history[1 if has_system else 0 :]
    tool_result_ids = {
        message.get("tool_call_id")
        for message in recent
        if message.get("role") == "tool" and message.get("tool_call_id")
    }
    valid_call_ids = set()
    filtered = []
    for message in recent:
        calls = message.get("tool_calls") or []
        if message.get("role") == "assistant" and calls:
            ids = {call.get("id") for call in calls}
            if not ids or not ids.issubset(tool_result_ids):
                continue
            valid_call_ids.update(ids)
        elif message.get("role") == "tool":
            if message.get("tool_call_id") not in tool_result_ids:
                continue
        filtered.append(dict(message))

    filtered = [
        message
        for message in filtered
        if message.get("role") != "tool"
        or message.get("tool_call_id") in valid_call_ids
    ]

    def clip(value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        marker = "\n[older context omitted]\n"
        if limit <= len(marker):
            return value[:limit]
        half = max(0, (limit - len(marker)) // 2)
        return value[:half] + marker + value[-half:]

    def encoded_size(items: list[dict[str, Any]]) -> int:
        return len(json.dumps(items, ensure_ascii=False, separators=(",", ":")))

    runtime_system = {"role": "system", "content": system_suffix}
    if encoded_size([runtime_system]) > max_chars - 64:
        raise ValueError("Runtime context exceeds the configured context budget.")
    static_limit = min(
        4_000,
        max_chars - encoded_size([runtime_system]) - 1_024,
    )
    system_content = f"{clip(system_base, max(0, static_limit))}{system_suffix}"
    system = {"role": "system", "content": system_content}
    if encoded_size([system]) > max_chars:
        raise ValueError("System and runtime context exceed the configured budget.")

    for message in filtered:
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = clip(content, 4_000)

    results = {
        message.get("tool_call_id"): message
        for message in filtered
        if message.get("role") == "tool"
    }
    groups: list[list[dict[str, Any]]] = []
    for message in filtered:
        if message.get("role") == "tool":
            continue
        calls = message.get("tool_calls") or []
        if calls:
            groups.append([message, *(results[call["id"]] for call in calls)])
        else:
            groups.append([message])

    selected: list[dict[str, Any]] = []
    for group in reversed(groups):
        if len(selected) + len(group) > max_messages:
            continue
        candidate = [*group, *selected]
        if encoded_size([system, *candidate]) <= max_chars:
            selected = candidate
            continue
        if len(group) != 1 or not isinstance(group[0].get("content"), str):
            continue

        message = dict(group[0])
        content = message["content"]
        low, high = 0, len(content)
        best = None
        while low <= high:
            middle = (low + high) // 2
            message["content"] = clip(content, middle)
            if encoded_size([system, message, *selected]) <= max_chars:
                best = dict(message)
                low = middle + 1
            else:
                high = middle - 1
        if best is not None:
            selected = [best, *selected]

    return [system, *selected]
