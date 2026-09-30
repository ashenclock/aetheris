from unittest.mock import patch

import pytest

from nexus.skills.read_file import ReadFileSkill


@pytest.mark.asyncio
async def test_sensitive_files_require_approval(tmp_path):
    secret = tmp_path / ".env"
    secret.write_text("FAKE_KEY=fixture\n", encoding="utf-8")
    skill = ReadFileSkill(tmp_path)

    with patch("rich.prompt.Confirm.ask", return_value=False):
        assert not await skill.confirm({"filepath": ".env"})
    with patch("rich.prompt.Confirm.ask", return_value=True):
        assert await skill.confirm({"filepath": ".env"})


@pytest.mark.asyncio
async def test_normal_files_remain_readable_without_approval(tmp_path):
    normal = tmp_path / "README.md"
    normal.write_text("safe fixture\n", encoding="utf-8")
    skill = ReadFileSkill(tmp_path)

    assert await skill.confirm({"filepath": "README.md"})
    assert await skill.execute(filepath="README.md") == "safe fixture\n"
