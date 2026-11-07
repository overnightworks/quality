"""The door's `gh` boundary: every `gh` command the door sends, and the reads of a repository's files at a commit."""

import subprocess
from collections.abc import Callable, Sequence

NOT_FOUND = "404"

GhRunner = Callable[[Sequence[str], str | None], str]


class GhFailed(Exception):
    pass


def run_gh(arguments: Sequence[str], stdin: str | None = None) -> str:
    try:
        done = subprocess.run(["gh", *arguments], input=stdin, capture_output=True, text=True, check=False)  # noqa: S603, S607 - argv is the fixed gh executable plus arguments and never a shell
    except OSError as error:
        raise GhFailed(f"cannot run gh: {error}") from error
    if done.returncode != 0:
        raise GhFailed(f"gh {' '.join(arguments[:3])} failed: {done.stderr.strip()}")
    return done.stdout


def file_at(gh: GhRunner, repository: str, path: str, sha: str) -> str | None:
    try:
        return gh(["api", "-H", "Accept: application/vnd.github.raw", f"repos/{repository}/contents/{path}?ref={sha}"], None)
    except GhFailed as error:
        if NOT_FOUND in str(error):
            return None
        raise
