from __future__ import annotations

import os
from pathlib import Path
from typing import Any


SUPPORTED_AUDIO_SUFFIXES = {".flac", ".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".ogg", ".wav", ".webm"}
MAX_AUDIO_BYTES = 25 * 1024 * 1024


class TranscriptionError(RuntimeError):
    """Raised when an API transcription cannot be started or parsed."""


def transcribe_file(
    audio_path: str | Path,
    *,
    model: str = "gpt-transcribe",
    language: str | None = None,
    prompt: str | None = None,
    response_format: str = "json",
) -> dict[str, Any]:
    path = Path(audio_path).expanduser().resolve()
    if not path.is_file():
        raise TranscriptionError(f"Audio file does not exist: {path}")
    if path.suffix.lower() not in SUPPORTED_AUDIO_SUFFIXES:
        raise TranscriptionError(
            f"Unsupported audio format '{path.suffix}'. Supported: {', '.join(sorted(SUPPORTED_AUDIO_SUFFIXES))}."
        )
    if path.stat().st_size > MAX_AUDIO_BYTES:
        raise TranscriptionError("Audio files are limited to 25 MB by the API path used here.")
    if not os.getenv("OPENAI_API_KEY"):
        raise TranscriptionError("OPENAI_API_KEY is not configured; the key is never read from CLI arguments.")

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise TranscriptionError(
            "The optional OpenAI SDK is missing. Install with: pip install -e '.[transcription]'"
        ) from exc

    request: dict[str, Any] = {
        "model": model,
        "response_format": response_format,
    }
    if language:
        request["language"] = language
    if prompt:
        request["prompt"] = prompt

    try:
        with path.open("rb") as audio_file:
            result = OpenAI().audio.transcriptions.create(file=audio_file, **request)
    except Exception as exc:
        raise TranscriptionError(f"Transcription API request failed: {exc}") from exc

    if isinstance(result, str):
        return {"text": result, "model": model, "response_format": response_format}
    text = getattr(result, "text", None)
    if not isinstance(text, str):
        raise TranscriptionError("The transcription response did not contain text.")
    payload = {"text": text, "model": model, "response_format": response_format}
    if hasattr(result, "model_dump"):
        payload["response"] = result.model_dump()
    return payload
