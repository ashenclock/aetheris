import pytest
from pydantic import ValidationError

from nexus.skills.search_code import SearchCodeSkill


@pytest.mark.asyncio
async def test_search_is_literal_case_insensitive_and_skips_generated_dirs(tmp_path):
    source = tmp_path / "src/example.py"
    source.parent.mkdir()
    source.write_text("class SafeTarget:\n    pass\n", encoding="utf-8")
    generated = tmp_path / ".venv/lib/site-packages/example.py"
    generated.parent.mkdir(parents=True)
    generated.write_text("safetarget in generated files", encoding="utf-8")

    result = await SearchCodeSkill(tmp_path).execute(
        pattern="SAFETARGET", extension=".py"
    )

    assert "src/example.py:1" in result
    assert ".venv" not in result


@pytest.mark.asyncio
async def test_search_does_not_read_symlinked_files_outside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    private = tmp_path / "private.py"
    private.write_text("outside-secret-marker", encoding="utf-8")
    try:
        (workspace / "linked.py").symlink_to(private)
    except OSError:
        return

    result = await SearchCodeSkill(workspace).execute(pattern="outside-secret-marker")

    assert result.startswith("No results found")
    assert "linked.py:" not in result


@pytest.mark.asyncio
async def test_search_limits_results_and_rejects_unbounded_arguments(tmp_path):
    (tmp_path / "one.py").write_text("needle\nneedle\n", encoding="utf-8")
    (tmp_path / "two.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / "large.py").write_text("needle" + " " * 1_000_000, encoding="utf-8")

    result = await SearchCodeSkill(tmp_path).execute(pattern="needle", max_results=1)
    assert result.startswith("Found 1 results")
    bounded = await SearchCodeSkill(tmp_path).execute(pattern="needle", max_results=200)
    assert "large.py" not in bounded
    with pytest.raises(ValidationError):
        SearchCodeSkill(tmp_path).parameters_schema.model_validate(
            {"pattern": "needle", "max_results": 201}
        )
    direct = await SearchCodeSkill(tmp_path).execute(pattern="needle", max_results=201)
    assert direct.startswith("Error: invalid search arguments:")
