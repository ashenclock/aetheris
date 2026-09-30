import tarfile

import pytest

from nexus.web.github import (
    GitHubRepositoryError,
    _validate_archive_members,
    parse_repository_url,
)


def test_parse_public_repository_url():
    repository = parse_repository_url("https://github.com/ashenclock/aetheris.git")
    assert repository.owner == "ashenclock"
    assert repository.name == "aetheris"


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/ashenclock/aetheris",
        "https://github.com/ashenclock/aetheris/issues/1",
        "https://example.com/ashenclock/aetheris",
    ],
)
def test_parse_rejects_non_repository_urls(url):
    with pytest.raises(GitHubRepositoryError):
        parse_repository_url(url)


def _member(name: str, *, kind: str = "file") -> tarfile.TarInfo:
    member = tarfile.TarInfo(name)
    if kind == "directory":
        member.type = tarfile.DIRTYPE
    elif kind == "symlink":
        member.type = tarfile.SYMTYPE
        member.linkname = "/tmp/outside"
    else:
        member.size = 0
    return member


def test_archive_validation_accepts_regular_files_and_directories(tmp_path):
    _validate_archive_members(
        [_member("repo/", kind="directory"), _member("repo/README.md")],
        tmp_path,
    )


@pytest.mark.parametrize(
    "member",
    [
        _member("../outside.txt"),
        _member("repo/link", kind="symlink"),
    ],
)
def test_archive_validation_rejects_unsafe_entries(tmp_path, member):
    with pytest.raises(GitHubRepositoryError):
        _validate_archive_members([member], tmp_path)


def test_archive_validation_rejects_special_files(tmp_path):
    member = tarfile.TarInfo("repo/device")
    member.type = tarfile.CHRTYPE
    with pytest.raises(GitHubRepositoryError):
        _validate_archive_members([member], tmp_path)
