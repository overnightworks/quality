"""How a test starts the door: in this process, where it injects what the door takes, or as a process from `MODULE_DOOR`."""

import contextlib
import hashlib
import os
import sys
from pathlib import Path

from quality import cli
from quality.pin import JudgingRelease
from quality.ratchet import BASELINE_PATH
from quality.report import Release

CHECK_WORD = "check"
ROOT = Path(__file__).parents[2]
# The driver runs from the fixture repository, so the root that holds `tests` is put on its path here, not by PYTHONPATH.
LAUNCH_DRIVER = f"import runpy, sys; sys.path.insert(0, {str(ROOT)!r}); runpy.run_module('tests.support.door_process', run_name='__main__')"
MODULE_DOOR = [sys.executable, "-c", LAUNCH_DRIVER]
# The real entry point, which judges with the running installation at the fixture's own gate pin.
QUALITY_MODULE_DOOR = [sys.executable, "-m", "quality.cli"]
STAGED_WORDS = (CHECK_WORD, "secrets", "--staged")
FIX_WORDS = (CHECK_WORD, "debt", "--fix")
DRY_RUN_WORDS = (*FIX_WORDS, "--dry-run")


def pinned_release() -> JudgingRelease:
    """The release every test door judges at, unless a test passes its own `judging`."""
    return JudgingRelease(Release("v0.10.0"), "pinned in .github/workflows/ci.yml")


def judging_at_the_pin(_root: Path, _command: str, _staged: bool) -> JudgingRelease:
    return pinned_release()


RELEASE_ROW = "  · release       v0.10.0        pinned in .github/workflows/ci.yml\n"


class PinnedJudging:
    """The door's `judging` at `pinned_release`, which keeps whether a check asked it."""

    def __init__(self) -> None:
        self.asked = False

    def __call__(self, root: Path, command: str, staged: bool) -> JudgingRelease:
        self.asked = True
        return judging_at_the_pin(root, command, staged)

    def frame(self, out: str) -> str:
        """`out` after its first line, which is the release row wherever a check asked."""
        if not self.asked:
            return out
        assert out.startswith(RELEASE_ROW), out
        return out.removeprefix(RELEASE_ROW)


def door_in(folder: Path, *words: str, **keywords) -> int:
    """The exit of `quality <words>` started in `folder`, judged at `pinned_release` unless `judging` is passed: the door
    reads the folder it starts in, and the test's own folder comes back."""
    with contextlib.chdir(folder):
        return cli.main(list(words), **{"judging": judging_at_the_pin, **keywords})


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


def fixed(capsys, repo: Path, *words: str, **keywords) -> tuple[int, str, str]:
    """The exit, stdout after the release row and stderr of `quality <words>` started in `repo`."""
    judging = PinnedJudging()
    code = door_in(repo, *words, clock=lambda: 0.0, judging=judging, **keywords)
    printed = capsys.readouterr()
    return code, judging.frame(printed.out), printed.err


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
