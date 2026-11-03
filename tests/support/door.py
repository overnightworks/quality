"""How a test starts the door: in this process, where it injects what the door takes, or as a process from `MODULE_DOOR`."""

import contextlib
import os
import sys
from pathlib import Path

from quality import cli

CHECK_WORD = "check"
MODULE_DOOR = [sys.executable, "-m", "quality.cli"]
STAGED_WORDS = (CHECK_WORD, "secrets", "--staged")
FIX_WORDS = (CHECK_WORD, "debt", "--fix")


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
