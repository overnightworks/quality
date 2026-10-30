"""The checkout as quality reads it: git's listing of the tracked files and their index modes, what the tree holds of
them, which of them are workflow files, and the turning of a refused read into `cannot judge`."""

import os
import re
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath

from quality import process, repository
from quality.report import CannotJudge, shell_word

WORKFLOW_DIRECTORY = PurePosixPath(".github/workflows")
YAML_SUFFIXES = frozenset({".yml", ".yaml"})
INDEX_MODE = re.compile(r"[0-7]{6}")
# git's sentence when the search up from the path finds no repository, read in the C locale; a git that words it
# differently leaves the refusal general, which stays true.
NO_REPOSITORY_SENTENCE = b"fatal: not a git repository (or any"


class OutsideWorkTree(CannotJudge):
    """The path lies in no work tree: git says so, or the path is a bare repository or the inside of `.git`."""

    def __init__(self, path: Path, message: str) -> None:
        super().__init__(message)
        self.path = path


def _refusal_text(path: Path, because: str) -> str:
    word = shell_word(str(path))
    says_why = f"`git -C {word} status` says why" if word else "`git status` run inside it says why"
    return f"{path} is not a readable git repository: {because} ({says_why})"


def _refusal(path: Path, because: str) -> CannotJudge:
    return CannotJudge(_refusal_text(path, because))


def _refusal_without_git(path: Path, what: str, reason: object) -> CannotJudge:
    return CannotJudge(f"{path} is not a readable git repository: {what} ({reason})")


def _git_listing(path: Path, command: list[str]) -> subprocess.CompletedProcess:
    """git's answer for the repository at `path`, asked in the C locale so its words read the same everywhere; one that
    refuses, or does not run, is a repository git cannot read."""
    try:
        done = process.run(["git", "-C", str(path.absolute()), *command], env={**os.environ, "LC_ALL": "C"}, encoding=None)
    except OSError as error:
        raise _refusal_without_git(path, "the working directory cannot be read", repository.os_error_reason(error)) from error
    except process.ProcessError as error:
        reason = repository.os_error_reason(error.__cause__) if isinstance(error.__cause__, OSError) else error
        raise _refusal_without_git(path, "git did not run", reason) from error
    if done.returncode != 0:
        # git's own words name the checkout it resolved, so only the exit code is printed and git is asked again.
        because = f"git {command[0]} exited {done.returncode}"
        if done.stderr.startswith(NO_REPOSITORY_SENTENCE):
            raise OutsideWorkTree(path, _refusal_text(path, because))
        raise _refusal(path, because)
    return done


def work_tree_top(path: Path) -> Path:
    """The top of the work tree `path` lies in, as `path` reaches it; `--show-cdup` never prints where the checkout lies."""
    answer = _git_listing(path, ["rev-parse", "--is-inside-work-tree", "--show-cdup"]).stdout
    inside, _, cdup = os.fsdecode(answer).strip().partition("\n")
    if inside != "true":
        raise OutsideWorkTree(path, _refusal_text(path, "it lies outside any work tree"))
    return path / cdup


def tracked_files(root: Path) -> list[str]:
    listing = _git_listing(root, ["ls-files", "-z", "--"])
    return [os.fsdecode(name) for name in listing.stdout.split(b"\0") if name]


def index_modes(root: Path) -> dict[str, str]:
    """Each tracked path's index mode; the index is the only source of a mode, and a link is never followed."""
    listing = _git_listing(root, ["ls-files", "-z", "-s", "--"])
    modes: dict[str, str] = {}
    for record in listing.stdout.split(b"\0"):
        if not record:
            continue
        fields, tab, name = os.fsdecode(record).partition("\t")
        parts = fields.split(" ")
        if not tab or len(parts) != 3 or not INDEX_MODE.fullmatch(parts[0]):
            raise CannotJudge(f"git ls-files -s printed an index record quality cannot read in {root}")
        modes[name] = parts[0]
    return modes


def judged[T](ask: Callable[[Path, str], T], root: Path, name: str) -> T:
    """Asks the repository reader; a file it cannot use cannot be judged."""
    try:
        return ask(root, name)
    except repository.RepositoryFileError as error:
        raise CannotJudge(str(error)) from error


def on_disk(root: Path, names: Sequence[str]) -> list[str]:
    """The tracked names the tree holds: a file deleted, sparse or linked to nowhere is gone, one the filesystem refuses is not."""
    return [name for name in names if judged(repository.is_on_disk, root, name)]


def read_existing[T](read: Callable[[Path, str], T | None], root: Path, names: Sequence[str]) -> dict[str, T]:
    """The content of each name the tree holds; a name that vanished since it was listed is gone."""
    return {name: content for name in names if (content := judged(read, root, name)) is not None}


def is_workflow(path: PurePosixPath) -> bool:
    return path.parent == WORKFLOW_DIRECTORY and path.suffix in YAML_SUFFIXES
