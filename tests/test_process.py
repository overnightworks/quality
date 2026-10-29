"""process.run and process.git: bytes on request, one failure path, and no child outliving its command."""

import os
import signal
import subprocess
import sys
import time
from contextlib import suppress

import pytest

from quality import process
from quality.process import ProcessError
from tests.support.processes import process_is_gone


@pytest.mark.integration
def test_git_keeps_bytes_when_asked_and_reads_as_text_otherwise():
    assert process.git(["--version"], "no git", encoding=None).startswith(b"git version")
    assert process.git(["--version"], "no git").startswith("git version")


@pytest.mark.integration
@pytest.mark.parametrize("encoding", ["utf-8", None])
def test_a_failing_git_is_a_process_error_of_the_failure_and_its_own_words(tmp_path, encoding):
    with pytest.raises(ProcessError, match=r"^cannot read: .*not a git repository"):
        process.git(["-C", str(tmp_path), "rev-parse", "HEAD"], "cannot read", {"PATH": os.environ["PATH"]}, encoding=encoding)


@pytest.mark.integration
def test_a_bounded_command_that_runs_out_of_time_takes_its_children_with_it(tmp_path):
    child = tmp_path / "child-pid"
    with pytest.raises(ProcessError, match="sh did not finish within 0.5 seconds"):
        process.run_bounded_in_own_group(["sh", "-c", f'sleep 300 & echo $! > "{child}"; wait'], 0.5)
    assert process_is_gone(int(child.read_text()))


@pytest.mark.integration
def test_a_bounded_command_returns_at_its_bound_though_a_descendant_left_the_group(tmp_path):
    escaped = tmp_path / "escaped-pid"
    started = time.monotonic()
    try:
        with pytest.raises(ProcessError, match="did not finish within 1 seconds"):
            process.run_bounded_in_own_group(["sh", "-c", f'setsid sleep 6 & echo $! > "{escaped}"; sleep 30'], 1)
        assert time.monotonic() - started < 4
    finally:
        if escaped.exists():
            escaped_pid = int(escaped.read_text())
            with suppress(ProcessLookupError):
                os.kill(escaped_pid, signal.SIGKILL)
            assert process_is_gone(escaped_pid)


@pytest.mark.integration
def test_a_bound_that_cuts_a_utf8_character_reports_the_timeout():
    with pytest.raises(ProcessError, match="did not finish within 0.5 seconds"):
        process.run_bounded_in_own_group(["sh", "-c", r"printf '\303'; sleep 30"], 0.5)


@pytest.mark.integration
def test_a_bounded_command_keeps_bytes_when_asked():
    done = process.run_bounded_in_own_group(["printf", "caf\\351"], 5, encoding=None)
    assert done.stdout == b"caf\xe9"


SIGTERM_OUTSIDE_SCRATCH = """
import sys
from quality import process
process.run(["sh", "-c", f'sleep 300 & echo $! > "{sys.argv[1]}"; wait'])
"""


@pytest.mark.integration
def test_a_sigterm_to_the_process_group_leaves_no_engine_running(tmp_path):
    """A caller outside a scratch directory has no SIGTERM handler: its engine stays in the group the signal reaches."""
    child = tmp_path / "child-pid"
    caller = subprocess.Popen([sys.executable, "-c", SIGTERM_OUTSIDE_SCRATCH, str(child)], start_new_session=True)
    try:
        deadline = time.monotonic() + 30
        while not (child.exists() and child.read_text().strip()):
            assert time.monotonic() < deadline, "the engine never started"
            time.sleep(0.05)
        os.killpg(caller.pid, signal.SIGTERM)
        caller.wait(timeout=30)
    finally:
        with suppress(ProcessLookupError):
            os.killpg(caller.pid, signal.SIGKILL)
    assert process_is_gone(int(child.read_text()))


def test_the_git_environment_cuts_off_every_attributes_source(tmp_path, monkeypatch):
    """#21 rule 1: the git environment excludes the system file, forces an empty attributes file, drops other GIT_*."""
    monkeypatch.setenv("GIT_DIR", "/elsewhere")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "5")
    environment = process.attribute_free_git_environment(tmp_path / "home")
    assert {name: value for name, value in environment.items() if name.startswith("GIT_")} == {
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_ATTR_SOURCE": process.EMPTY_TREE,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.attributesFile",
        "GIT_CONFIG_VALUE_0": os.devnull,
    }


@pytest.mark.parametrize(
    ("version_output", "expected"),
    [
        ("git version 2.39.3 (Apple Git-146)", "2.39.3"),
        ("git version 2.41.0.windows.1", "2.41.0"),
        ("git version 2.40", "2.40"),
        ("git version 2.9.5", "2.9.5"),
        ("git version 2.53.0", "2.53.0"),
        ("git version 2.0000041", "2.0000041"),
        ("git version 12345.6", "12345.6"),
        ("git version 2.41.0-rc0", "2.41.0"),
        ("git version v2.41.0", "2.41.0"),
        ("git version 2.43.0-rc1", "2.43.0"),
        ("git version 2.41.0.dirty", "2.41.0"),
        ("git version 2.41.x", "2.41"),
        ("git version unknown", None),
        ("", None),
        ("9" * 50_000, None),
    ],
)
def test_the_git_version_is_the_first_dotted_number_in_the_output(version_output, expected):
    """#31 rule 2 (S8786): the version is found as the old pattern found it, in any word, and on any digit length."""
    assert process.parse_git_version(version_output) == expected
