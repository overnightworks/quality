from pathlib import Path

from tests.support.git_repositories import git

HTTPS = "https://github.com/{repository}"
SSH_URL = "ssh://git@github.com/{repository}.git"
SCP = "git@github.com:{repository}.git"


def with_github_remote(
    repo: Path, repository: str = "acme/guidance", form: str = HTTPS, remote: str = "origin"
) -> Path:
    """Adds the GitHub remote `remote` for `repository`, spelled as `form`, to `repo` and returns it."""
    git(repo, "remote", "add", remote, form.format(repository=repository))
    return repo
