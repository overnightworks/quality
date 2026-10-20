import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from quality import cli

DOOR = [sys.executable, "-m", "quality.cli"]
STOP_LINE = re.compile(r"::stop-commands::([0-9a-f]{32})")
CLOSED_STDOUT = 'exec "$@" >&-'
CLOSED_STDERR = 'exec "$@" 2>&-'


def run_door(*arguments: str, cwd: Path, actions: str | None = "true", shell: str | None = None):
    environment = {key: value for key, value in os.environ.items() if key != "GITHUB_ACTIONS"}
    if actions is not None:
        environment["GITHUB_ACTIONS"] = actions
    command = [*DOOR, *arguments] if shell is None else ["sh", "-c", shell, "sh", *DOOR, *arguments]
    return subprocess.run(command, cwd=cwd, env=environment, capture_output=True, text=True, check=False)


@pytest.fixture
def clean_repo(build_repo) -> Path:
    return build_repo({"README.md": "x\n"})


@pytest.fixture
def broken_compose_repo(build_repo) -> Path:
    return build_repo({"compose.yaml": "services: [\n"})


def stop_token(stream: str) -> str:
    first_line = stream.splitlines()[0]
    match = STOP_LINE.fullmatch(first_line)
    assert match, first_line
    return match.group(1)


def test_both_streams_start_with_the_same_stop_and_nothing_resumes_it(clean_repo):
    """#89 rule 1, cell 1."""
    local = run_door("hygiene", ".", cwd=clean_repo, actions=None)
    actions = run_door("hygiene", ".", cwd=clean_repo)
    token = stop_token(actions.stdout)
    assert stop_token(actions.stderr) == token
    assert actions.stdout.split("\n", 1)[1] == local.stdout
    assert actions.stderr.split("\n", 1)[1] == local.stderr
    assert f"::{token}::" not in actions.stdout + actions.stderr
    assert actions.returncode == local.returncode


def test_every_run_draws_a_new_token(clean_repo):
    """#89 rule 1, cell 2 (E8)."""
    tokens = {stop_token(run_door("hygiene", ".", cwd=clean_repo).stdout) for _ in range(2)}
    assert len(tokens) == 2


@pytest.mark.parametrize("actions", [None, "false", "TRUE", ""], ids=["unset", "false", "upper case", "empty"])
def test_outside_actions_the_door_prints_no_stop(clean_repo, actions):
    """#89 rule 1, cell 3 (E2)."""
    done = run_door("hygiene", ".", cwd=clean_repo, actions=actions)
    assert "stop-commands" not in done.stdout + done.stderr


def test_main_alone_prints_no_stop(clean_repo, monkeypatch, capsys):
    """#89 rule 1, cell 4: the stop belongs to the process entry."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    cli.main(["hygiene", str(clean_repo)])
    captured = capsys.readouterr()
    assert "stop-commands" not in captured.out + captured.err


def test_a_closed_stdout_leaves_the_stop_and_the_verdict_on_stderr(broken_compose_repo):
    """#89 rule 1, cell 5: fd 1 closed."""
    done = run_door("hygiene", ".", cwd=broken_compose_repo, shell=CLOSED_STDOUT)
    stop_token(done.stderr)
    assert (done.returncode, "compose-port cannot judge" in done.stderr) == (2, True)


def test_a_closed_stderr_keeps_the_stop_on_stdout_and_the_exit(broken_compose_repo):
    """#89 rule 1, cell 5: fd 2 closed."""
    open_door = run_door("hygiene", ".", cwd=broken_compose_repo)
    done = run_door("hygiene", ".", cwd=broken_compose_repo, shell=CLOSED_STDERR)
    stop_token(done.stdout)
    assert done.returncode == open_door.returncode == 2


@pytest.mark.parametrize(
    "arguments",
    [("baseline", "write", "."), ("engines", "verify", "engines"), ("sonar", "preflight", ".")],
    ids=["baseline write", "engines verify", "sonar preflight"],
)
def test_other_doors_start_with_the_stop_too(clean_repo, arguments):
    """#89 rule 1, cell 6."""
    (clean_repo / "engines").mkdir()
    done = run_door(*arguments, cwd=clean_repo)
    assert stop_token(done.stdout) == stop_token(done.stderr)
