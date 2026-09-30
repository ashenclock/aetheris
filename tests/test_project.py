import json

import pytest

from nexus.project import add_mcp_server, create_agent, create_skill, load_mcp_config


def test_project_scaffolding_is_local_and_reviewable(tmp_path):
    skill = create_skill(tmp_path, "reviewer", "Review the project")
    agent = create_agent(tmp_path, "security", "Review security")
    config = add_mcp_server(
        tmp_path,
        "demo",
        "python tests/fixtures/mcp_demo_server.py",
        env=["DEMO_TOKEN"],
    )

    assert skill == tmp_path / ".aetheris/skills/reviewer/SKILL.md"
    assert agent == tmp_path / ".aetheris/agents/security.md"
    assert "untrusted data" in skill.read_text(encoding="utf-8")
    assert json.loads(config.read_text(encoding="utf-8"))["mcpServers"]["demo"] == {
        "command": "python",
        "args": ["tests/fixtures/mcp_demo_server.py"],
        "env": ["DEMO_TOKEN"],
        "enabled": True,
    }
    assert load_mcp_config(tmp_path)["mcpServers"]["demo"]["command"] == "python"


@pytest.mark.parametrize(
    "server, message",
    [
        (None, "must be a JSON object"),
        ("malformed", "must be a JSON object"),
        ({"command": "  "}, "non-empty command"),
        ({"command": "python", "args": "not-a-list"}, "string list"),
        ({"command": "python", "env": [1]}, "string list"),
        ({"command": "python", "enabled": "yes"}, "must be boolean"),
    ],
)
def test_mcp_config_rejects_malformed_server_entries(tmp_path, server, message):
    path = tmp_path / ".aetheris/mcp.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"mcpServers": {"demo": server}}), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_mcp_config(tmp_path)
