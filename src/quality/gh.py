"""The door's `gh` boundary: every `gh` command the door sends, and the reads of a repository's files at a commit."""

import json
import subprocess
import unicodedata
from collections.abc import Callable, Sequence
from urllib.parse import quote

from quality import checkout
from quality.checkout import Unread
from quality.report import CannotJudge

NOT_FOUND = "404"
# A character that could change the path or the query of the next request, beside every control character.
REQUEST_CHARACTERS = frozenset("/\\%?#")

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


def workflow_texts_at(gh: GhRunner, repository: str, sha: str) -> dict[str, str | Unread]:
    """The text of each workflow file the repository's workflow directory lists at `sha`, read at that commit; a
    listing whose names could reshape a request, or that holds a workflow quality cannot follow, refuses whole."""
    listing = f"the workflow listing of {repository} at {sha[:7]}"
    try:
        entries = _listing_at(gh, repository, sha, listing)
        texts: dict[str, str | Unread] = {}
        for name, kind in entries:
            if kind not in {"file", "symlink"}:
                continue
            if _shapes_a_request(name):
                raise CannotJudge(f"{listing} is not a list of files")
            path = checkout.WORKFLOW_DIRECTORY / name
            if not checkout.is_workflow(path):
                continue
            if kind == "symlink":
                raise CannotJudge(f"{listing} holds the symlink {name}")
            text = file_at(gh, repository, f"{checkout.WORKFLOW_DIRECTORY}/{quote(name, safe='')}", sha)
            texts[str(path)] = Unread.ABSENT if text is None else text
    except GhFailed as error:
        raise CannotJudge(f"cannot read the workflows of {repository} at {sha[:7]}: {error}") from error
    return texts


def _shapes_a_request(name: str) -> bool:
    """Whether a listed name, read raw, is no single path segment or could change the request's path or query."""
    return name in {"", ".", ".."} or any(character in REQUEST_CHARACTERS or unicodedata.category(character) == "Cc" for character in name)


def _listing_at(gh: GhRunner, repository: str, sha: str, listing: str) -> list[tuple[str, str]]:
    """Each entry's name and type, as JSON names them; no workflow directory is an empty listing."""
    try:
        answer = gh(["api", f"repos/{repository}/contents/{checkout.WORKFLOW_DIRECTORY}?ref={sha}"], None)
    except GhFailed as error:
        if NOT_FOUND in str(error):
            return []
        raise
    try:
        entries = json.loads(answer)
    except ValueError:
        entries = None
    if not isinstance(entries, list) or not all(
        isinstance(entry, dict) and isinstance(entry.get("name"), str) and isinstance(entry.get("type"), str) for entry in entries
    ):
        raise CannotJudge(f"{listing} is not a list of files")
    return [(entry["name"], entry["type"]) for entry in entries]
