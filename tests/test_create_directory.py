import pytest

from nexus.skills.create_directory import CreateDirectorySkill


@pytest.mark.asyncio
async def test_create_directory_is_scoped_and_idempotent(tmp_path):
    skill = CreateDirectorySkill(tmp_path)
    assert skill.requires_confirmation
    for _ in range(2):
        assert "Directory ready" in await skill.execute(path="ml/iris")
    assert (tmp_path / "ml/iris").is_dir()
    assert "outside the workspace" in await skill.execute(path="../outside")
    (tmp_path / "link").symlink_to(tmp_path.parent, target_is_directory=True)
    assert "outside the workspace" in await skill.execute(path="link/outside")
    (tmp_path / "file").write_text("fixture")
    assert "Error" in await skill.execute(path="file")
