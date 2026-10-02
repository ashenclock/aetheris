from nexus.core.events import redact, format_event


def test_activity_redacts_literal_credentials_and_environment_values(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-secret-key")
    assert redact({"env": {"API_KEY": "not-in-process-env"}}) == {
        "env": {"API_KEY": "<redacted>"}
    }
    message = format_event(
        "tool_output",
        {
            "name": "run_command",
            "preview": "fixture-secret-key Bearer token-value password=another-secret",
        },
    )
    assert all(
        secret not in message
        for secret in ("fixture-secret-key", "token-value", "another-secret")
    )
    assert redact({"completion_tokens": 5, "tool_call_id": "call-1"}) == {
        "completion_tokens": 5,
        "tool_call_id": "call-1",
    }
