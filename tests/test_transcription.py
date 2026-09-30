import pytest

from nexus.transcription import TranscriptionError, transcribe_file


def test_transcription_rejects_unsupported_format(tmp_path):
    audio = tmp_path / "notes.txt"
    audio.write_text("not audio", encoding="utf-8")

    with pytest.raises(TranscriptionError, match="Unsupported audio format"):
        transcribe_file(audio)


def test_transcription_requires_api_key(tmp_path, monkeypatch):
    audio = tmp_path / "notes.wav"
    audio.write_bytes(b"not really audio")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(TranscriptionError, match="OPENAI_API_KEY"):
        transcribe_file(audio)
