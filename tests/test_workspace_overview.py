import pytest

from nexus.skills.workspace_overview import WorkspaceOverviewSkill


@pytest.mark.asyncio
async def test_workspace_overview_is_bounded_and_omits_hidden_files(tmp_path):
    (tmp_path / "README.md").write_text("public project docs", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=never show", encoding="utf-8")
    nested = tmp_path / "src" / "package"
    nested.mkdir(parents=True)
    (nested / "module.py").write_text("pass", encoding="utf-8")

    result = await WorkspaceOverviewSkill(tmp_path).execute()

    assert "README.md" in result
    assert "src/package/" in result
    assert "src/package/module.py" in result
    assert ".env" not in result
    assert "SECRET" not in result
