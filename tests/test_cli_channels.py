import contextlib
import io
import itertools
import re
from pathlib import Path

import pytest

from quality import cli
from tests.support.check import refusal_pair
from tests.support.terminal import Terminal


class TurnsPlain(Terminal):
    """A terminal that answers as one the first time it is asked and as a pipe ever after."""

    def __init__(self) -> None:
        super().__init__()
        self.asked = False

    def isatty(self) -> bool:
        first, self.asked = not self.asked, True
        return first


def run_on(repo: Path, stdout: io.StringIO, stderr: io.StringIO, environment: dict[str, str]) -> int:
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        return cli.main(["hygiene", str(repo)], clock=lambda: 0.0, environment=environment)


def without_sgr(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


@pytest.mark.parametrize(("stdout_kind", "stderr_kind"), list(itertools.product((Terminal, io.StringIO), repeat=2)))
def test_each_stream_is_styled_on_its_own(build_repo, stdout_kind, stderr_kind):
    repo = build_repo({"compose.yaml": "services: [\n"})
    escape = "\x1b"
    stdout, stderr = stdout_kind(), stderr_kind()
    code = run_on(repo, stdout, stderr, {"TERM": "xterm"})
    assert (escape in stdout.getvalue(), escape in stderr.getvalue()) == (stdout_kind is Terminal, stderr_kind is Terminal)
    assert (without_sgr(stderr.getvalue()), code) == (refusal_pair(repo), 2)


@pytest.mark.parametrize(
    ("injected", "process_no_color", "styled"),
    [
        ({"TERM": "xterm"}, "1", True),
        ({"TERM": "xterm", "NO_COLOR": "1"}, None, False),
        ({"TERM": "dumb"}, None, False),
        ({"TERM": "xterm", "NO_COLOR": ""}, "1", True),
        ({}, None, True),
    ],
    ids=[
        "the process says no, the door was told nothing",
        "NO_COLOR injected",
        "dumb injected",
        "empty NO_COLOR injected",
        "no TERM",
    ],
)
def test_the_door_decides_style_from_the_environment_it_is_given(build_repo, monkeypatch, injected, process_no_color, styled):
    repo = build_repo({"compose.yaml": "services: [\n"})
    monkeypatch.setenv("TERM", "xterm")
    if process_no_color is None:
        monkeypatch.delenv("NO_COLOR", raising=False)
    else:
        monkeypatch.setenv("NO_COLOR", process_no_color)
    escape = "\x1b"
    stdout, stderr = Terminal(), Terminal()
    code = run_on(repo, stdout, stderr, injected)
    assert (escape in stdout.getvalue(), escape in stderr.getvalue(), without_sgr(stderr.getvalue()), code) == (styled, styled, refusal_pair(repo), 2)


def test_a_streams_style_holds_for_the_whole_run(build_repo, monkeypatch):
    repo = build_repo({"src/.env": "A=1\n"})
    monkeypatch.setenv("TERM", "xterm")
    for name in ("NO_COLOR", "PYTHON_COLORS", "FORCE_COLOR"):
        monkeypatch.delenv(name, raising=False)
    escape = "\x1b"
    stdout, stderr = TurnsPlain(), io.StringIO()
    code = run_on(repo, stdout, stderr, {"TERM": "xterm"})
    lines = stdout.getvalue().splitlines()
    assert (escape in lines[0], escape in lines[-1], stderr.getvalue(), code) == (True, True, "", 1)


def test_the_pairs_style_holds_for_both_its_lines(build_repo):
    repo = build_repo({"compose.yaml": "services: [\n"})
    escape = "\x1b"
    stdout, stderr = io.StringIO(), TurnsPlain()
    code = run_on(repo, stdout, stderr, {"TERM": "xterm"})
    lines = stderr.getvalue().splitlines()
    assert (escape in lines[0], escape in lines[-1], without_sgr(stderr.getvalue()), code) == (True, True, refusal_pair(repo), 2)
