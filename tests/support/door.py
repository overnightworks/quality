"""How a test starts the door: in this process, where it injects what the door takes, or as a process from `MODULE_DOOR`."""

import contextlib
import hashlib
import os
import sys
from pathlib import Path

from quality import cli
from quality.ratchet import BASELINE_PATH

CHECK_WORD = "check"
MODULE_DOOR = [sys.executable, "-m", "quality.cli"]
STAGED_WORDS = (CHECK_WORD, "secrets", "--staged")
FIX_WORDS = (CHECK_WORD, "debt", "--fix")
DRY_RUN_WORDS = (*FIX_WORDS, "--dry-run")


def door_in(folder: Path, *words: str, **keywords) -> int:
    """The exit of `quality <words>` started in `folder`: the door reads the folder it starts in, and the test's own folder comes back."""
    with contextlib.chdir(folder):
        return cli.main(list(words), **keywords)


def check_door(repository: Path, **keywords) -> int:
    return door_in(repository, CHECK_WORD, **keywords)


def staged_door(repository: Path, *words: str, **keywords) -> int:
    """The exit of `quality check secrets --staged <words>` started in `repository`."""
    return door_in(repository, *STAGED_WORDS, *words, **keywords)


def door_environment(**extra: str) -> dict[str, str]:
    """The process environment without the variables that change how the door paints or stops commands."""
    dropped = ("FORCE_COLOR", "GITHUB_ACTIONS", "NO_COLOR", "PYTHON_COLORS", "TERM")
    kept = {key: value for key, value in os.environ.items() if key not in dropped}
    return kept | extra


def fixed(capsys, repo: Path, *words: str) -> tuple[int, str, str]:
    """The exit, stdout and stderr of `quality <words>` started in `repo`."""
    code = door_in(repo, *words, clock=lambda: 0.0)
    printed = capsys.readouterr()
    return code, printed.out, printed.err


def digest(repo: Path) -> str:
    return hashlib.sha256((repo / BASELINE_PATH).read_bytes()).hexdigest()


def restore_step() -> str:
    return "restore quality/baseline.toml from git, or repair it by hand"


def tree_of(root: Path) -> dict[str, bytes | str | None]:
    """Every entry under `root` outside `.git`: a file's bytes, a link's target, a folder as None."""
    found: dict[str, bytes | str | None] = {}
    for current, folders, files in os.walk(root):
        folders[:] = [folder for folder in folders if folder != ".git"]
        for name in (*folders, *files):
            path = Path(current) / name
            key = os.fsdecode(path.relative_to(root))
            found[key] = os.readlink(path) if path.is_symlink() else None if path.is_dir() else path.read_bytes()
    return found
