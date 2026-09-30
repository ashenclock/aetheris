from __future__ import annotations

import io
import json
import tarfile
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

MAX_ARCHIVE_FILES = 10_000
MAX_ARCHIVE_MEMBERS = 20_000
MAX_EXTRACTED_BYTES = 100 * 1024 * 1024


class GitHubRepositoryError(RuntimeError):
    """Raised when a public GitHub repository cannot be downloaded safely."""


@dataclass(frozen=True)
class GitHubRepository:
    owner: str
    name: str


def _validate_archive_members(
    members: Iterable[tarfile.TarInfo],
    extraction_root: Path,
    *,
    max_files: int = MAX_ARCHIVE_FILES,
    max_members: int = MAX_ARCHIVE_MEMBERS,
    max_extracted_bytes: int = MAX_EXTRACTED_BYTES,
) -> None:
    """Reject unsafe archives and bound member count and expanded size."""
    files = 0
    total_size = 0
    member_count = 0
    root = extraction_root.resolve()
    for member in members:
        member_count += 1
        if member_count > max_members:
            raise GitHubRepositoryError("GitHub archive contains too many entries.")
        target = (extraction_root / member.name).resolve()
        if not target.is_relative_to(root):
            raise GitHubRepositoryError("GitHub archive contains an unsafe path.")
        if member.issym() or member.islnk():
            raise GitHubRepositoryError(
                "GitHub archive contains a symlink or hard link, which is not allowed."
            )
        if not (member.isdir() or member.isfile()):
            raise GitHubRepositoryError(
                "GitHub archive contains an unsupported special file."
            )
        if member.isfile():
            files += 1
            total_size += member.size
            if files > max_files or total_size > max_extracted_bytes:
                raise GitHubRepositoryError(
                    "GitHub archive exceeds the extraction file or size limit."
                )


def parse_repository_url(raw_url: str) -> GitHubRepository:
    parsed = urlparse(raw_url.strip())
    if parsed.scheme != "https" or parsed.netloc.lower() not in {
        "github.com",
        "www.github.com",
    }:
        raise GitHubRepositoryError("Use an https://github.com/OWNER/REPOSITORY URL.")

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2:
        raise GitHubRepositoryError(
            "The URL must point to a repository, not a file or issue."
        )
    owner, name = parts
    if name.endswith(".git"):
        name = name[:-4]
    if not owner or not name or any(char in owner + name for char in "\\\x00"):
        raise GitHubRepositoryError("The repository URL contains an invalid name.")
    return GitHubRepository(owner=owner, name=name)


def _request_bytes(url: str, *, max_bytes: int) -> bytes:
    request = Request(url, headers={"User-Agent": "aetheris-streamlit-demo"})
    try:
        with urlopen(request, timeout=30) as response:
            content_length = int(response.headers.get("Content-Length") or 0)
            if content_length > max_bytes:
                raise GitHubRepositoryError(
                    "The repository archive is larger than the demo limit."
                )
            chunks: list[bytes] = []
            total = 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise GitHubRepositoryError(
                        "The repository archive is larger than the demo limit."
                    )
                chunks.append(chunk)
            return b"".join(chunks)
    except HTTPError as exc:
        raise GitHubRepositoryError(
            f"GitHub returned HTTP {exc.code} for this repository."
        ) from exc
    except URLError as exc:
        raise GitHubRepositoryError(f"Could not reach GitHub: {exc.reason}") from exc


def default_branch(repository: GitHubRepository) -> str:
    url = f"https://api.github.com/repos/{repository.owner}/{repository.name}"
    try:
        payload = json.loads(_request_bytes(url, max_bytes=1_000_000))
        branch = payload.get("default_branch")
    except (json.JSONDecodeError, GitHubRepositoryError) as exc:
        raise GitHubRepositoryError(
            "Could not determine the repository default branch."
        ) from exc
    if not isinstance(branch, str) or not branch:
        raise GitHubRepositoryError("GitHub did not return a default branch.")
    return branch


def download_public_repository(
    raw_url: str,
    branch: str | None = None,
    *,
    max_bytes: int = 50 * 1024 * 1024,
) -> tuple[GitHubRepository, Path, tempfile.TemporaryDirectory[str]]:
    """Download a bounded public repository archive into an isolated temp directory."""
    repository = parse_repository_url(raw_url)
    selected_branch = (
        branch.strip() if branch and branch.strip() else default_branch(repository)
    )
    archive_url = (
        f"https://codeload.github.com/{repository.owner}/{repository.name}"
        f"/tar.gz/refs/heads/{selected_branch}"
    )
    archive = _request_bytes(archive_url, max_bytes=max_bytes)
    temporary = tempfile.TemporaryDirectory(prefix="aetheris-github-")
    extraction_root = Path(temporary.name) / "repository"
    extraction_root.mkdir()
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r|gz") as handle:
            _validate_archive_members(handle, extraction_root)
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as handle:
            handle.extractall(extraction_root)
    except (GitHubRepositoryError, tarfile.TarError, OSError) as exc:
        temporary.cleanup()
        if isinstance(exc, GitHubRepositoryError):
            raise
        raise GitHubRepositoryError(
            "GitHub returned an invalid repository archive."
        ) from exc

    roots = [path for path in extraction_root.iterdir() if path.is_dir()]
    if len(roots) != 1:
        temporary.cleanup()
        raise GitHubRepositoryError("The repository archive has an unexpected layout.")
    return repository, roots[0], temporary
