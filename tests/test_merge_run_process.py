"""`quality merge` with several names as real processes on a pseudo-terminal: a child that traps Ctrl-C, a child that dies
by a signal, input typed ahead, a working directory that holds a `quality` package, and the door's own children against a
fake `gh`."""

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.support.door import door_environment
from tests.support.merge import restore_line
from tests.support.merge_run import FIRST, SECOND, judging, row
from tests.support.merge_run_child import file_name
from tests.support.pty_session import PtySession

pytestmark = pytest.mark.integration

DRIVER = Path(__file__).parent / "support" / "merge_run_driver.py"
FAKE_GH = """#!{python}
import json, sys
path = sys.argv[2]
if "/pulls/" in path:
    print(json.dumps({{"state": "closed", "draft": False, "head": {{"sha": "a" * 40}}, "base": {{"ref": "main"}}, "body": ""}}))
else:
    print(json.dumps({{"permissions": {{"admin": True}}, "default_branch": "main"}}))
"""
SHADOWING_PACKAGE = "from pathlib import Path\nPath({marker!r}).write_text('loaded')\n"
CLOSED_PAIR = "error: not merged · #{number} is closed · nothing changed"


@dataclass
class Scene:
    """A directory the test and its children share: the plan of what each child does, the `release` FIFO the test writes
    to let a waiting child go on, a fake `gh` first on the path, and a working directory of its own."""

    root: Path

    @property
    def work(self) -> Path:
        return self.root / "work"

    def plan(self, **modes: str) -> None:
        (self.root / "plan.json").write_text(json.dumps({name: mode for name, mode in modes.items()}))

    def environment(self) -> dict[str, str]:
        path = f"{self.root / 'bin'}{os.pathsep}{os.environ['PATH']}"
        return door_environment(NO_COLOR="1", PATH=path, MERGE_CHILD_DIRECTORY=str(self.root))

    def release(self) -> None:
        (self.root / "release").write_text("go")

    def started(self, name: str) -> bool:
        return (self.root / f"started-{file_name(name)}").exists()

    def finished(self, name: str) -> bool:
        return (self.root / f"finished-{file_name(name)}").exists()

    def answer(self, name: str) -> str:
        return (self.root / f"answer-{file_name(name)}").read_text()

    def started_with(self, name: str) -> str:
        return (self.root / f"started-{file_name(name)}").read_text()

    def door(self, *words: str) -> PtySession:
        """`quality merge <words>` where every child is the fake child, started in the work directory."""
        return PtySession(["-P", str(DRIVER), *words], self.environment(), self.work)

    def real_door(self, *words: str) -> PtySession:
        """The real `quality merge <words>`: each child is the door itself, reading the fake `gh`."""
        return PtySession(["-P", "-m", "quality.cli", "merge", *words], self.environment(), self.work)


@pytest.fixture
def scene(tmp_path) -> Scene:
    built = Scene(tmp_path)
    built.work.mkdir()
    os.mkfifo(tmp_path / "release")
    (tmp_path / "bin").mkdir()
    gh = tmp_path / "bin" / "gh"
    gh.write_text(FAKE_GH.format(python=sys.executable))
    gh.chmod(0o755)
    return built


def judged_row(position: int, name: str) -> str:
    return judging(position, 2, name).rstrip("\n")


def lines_in_order(output: str, expected: list[str]) -> bool:
    """Whether each of `expected` is a line of `output` and they stand in that order."""
    remaining = iter(output.splitlines())
    return all(any(line == wanted for line in remaining) for wanted in expected)


def test_a_child_that_traps_ctrl_c_finishes_and_no_child_starts_after(scene):
    """MERGE-113: Ctrl-C reaches child and run together; the run waits for the child, which completes its work, never
    kills it, starts no further child and ends 130."""
    scene.plan(**{FIRST: "trap", SECOND: "exit:0"})
    with scene.door(FIRST, SECOND) as run:
        run.read_until(f"child {FIRST} ready")
        run.interrupt()
        run.read_until(f"child {FIRST} trapped")
        scene.release()
        code, out = run.finish()
    assert (code, scene.finished(FIRST), scene.started(SECOND)) == (130, True, False)
    assert lines_in_order(out, [row("✓", FIRST, "merged", width=14).rstrip("\n"), row("·", SECOND, "not run", width=14).rstrip("\n")])


def test_a_child_killed_by_sigkill_leaves_the_pair_with_the_restore_command_and_no_child_after(scene):
    """MERGE-114: a child that died by a signal other than SIGINT ends the run 2 with the gate possibly off and the
    restore command named first; no later child starts."""
    scene.plan(**{FIRST: "kill", SECOND: "exit:0"})
    with scene.door(FIRST, SECOND) as run:
        code, out = run.finish()
    assert (code, scene.started(SECOND)) == (2, False)
    assert lines_in_order(
        out,
        [
            f"error: quality_gate MAY STILL BE OFF on acme/widgets · {FIRST} was killed by signal 9 · merged 0 of 2 · 1 not run",
            "next:",
            restore_line().rstrip("\n"),
            f"    quality merge {SECOND}",
        ],
    )


def test_input_typed_while_a_child_runs_never_answers_the_next_childs_question(scene):
    """MERGE-110: a line typed during one pull request is discarded before the next starts, so that child's question
    waits for its own answer."""
    scene.plan(**{FIRST: "wait", SECOND: "ask"})
    with scene.door(FIRST, SECOND) as run:
        run.read_until(f"child {FIRST} ready")
        run.type("yes\n")
        run.read_until("yes")
        scene.release()
        run.read_until(f"child {SECOND} asking")
        run.type("second\n")
        code, _ = run.finish()
    assert (code, scene.answer(SECOND)) == (0, "second\n")


def test_two_closed_pull_requests_are_refused_in_order_and_summarised(scene):
    """MERGE-108, MERGE-111: each real child refuses its closed pull request as a run for it alone does, in the order
    named, and the run ends with a row per name and the pair that names both."""
    with scene.real_door("acme/a#1", "acme/b#2") as run:
        code, out = run.finish()
    assert code == 1
    assert lines_in_order(
        out,
        [
            judged_row(1, "acme/a#1"),
            CLOSED_PAIR.format(number=1),
            judged_row(2, "acme/b#2"),
            CLOSED_PAIR.format(number=2),
            row("!", "acme/a#1", "not merged").rstrip("\n"),
            row("!", "acme/b#2", "not merged").rstrip("\n"),
            "error: merged 0 of 2 · 2 not merged · nothing changed",
            "next: quality merge acme/a#1 acme/b#2",
        ],
    )


def test_a_quality_package_in_the_working_directory_is_never_loaded_by_a_child(scene):
    """MERGE-109: a child runs without the working directory on its path, so a pull request's checkout cannot put its own
    `quality` package under the operator's login."""
    marker = scene.root / "loaded"
    package = scene.work / "quality"
    package.mkdir()
    (package / "__init__.py").write_text(SHADOWING_PACKAGE.format(marker=str(marker)))
    with scene.real_door("acme/a#1", "acme/b#2") as run:
        code, out = run.finish()
    assert (code, marker.exists(), out.count("is closed")) == (1, False, 2)
