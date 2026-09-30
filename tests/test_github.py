import pytest

from nexus.web.github import GitHubRepositoryError, parse_repository_url


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
