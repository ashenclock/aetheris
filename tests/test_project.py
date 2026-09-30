import json

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
