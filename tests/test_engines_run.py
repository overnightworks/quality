"""engines.run and engines.git: bytes on request, one failure path, and no child outliving its command."""

import os
import signal
import subprocess
import sys
import time
from contextlib import suppress

import pytest

from quality import engines
from quality.engines import EngineError
from tests.support.processes import process_is_gone

pytestmark = pytest.mark.integration


def test_git_keeps_bytes_when_asked_and_reads_as_text_otherwise():
    assert engines.git(["--version"], "no git", encoding=None).startswith(b"git version")
    assert engines.git(["--version"], "no git").startswith("git version")


@pytest.mark.parametrize("encoding", ["utf-8", None])
def test_a_failing_git_is_an_engine_error_of_the_failure_and_its_own_words(tmp_path, encoding):
    with pytest.raises(EngineError, match=r"^cannot read: .*not a git repository"):
        engines.git(["-C", str(tmp_path), "rev-parse", "HEAD"], "cannot read", {"PATH": os.environ["PATH"]}, encoding=encoding)


def test_a_bounded_command_that_runs_out_of_time_takes_its_children_with_it(tmp_path):
    child = tmp_path / "child-pid"
    with pytest.raises(EngineError, match="sh did not finish within 0.5 seconds"):
        engines.run_bounded_in_own_group(["sh", "-c", f'sleep 300 & echo $! > "{child}"; wait'], 0.5)
    assert process_is_gone(int(child.read_text()))


def test_a_bounded_command_returns_at_its_bound_though_a_descendant_left_the_group(tmp_path):
    escaped = tmp_path / "escaped-pid"
    started = time.monotonic()
    try:
        with pytest.raises(EngineError, match="did not finish within 1 seconds"):
            engines.run_bounded_in_own_group(["sh", "-c", f'setsid sleep 6 & echo $! > "{escaped}"; sleep 30'], 1)
        assert time.monotonic() - started < 4
    finally:
        if escaped.exists():
            escaped_pid = int(escaped.read_text())
            with suppress(ProcessLookupError):
                os.kill(escaped_pid, signal.SIGKILL)
            assert process_is_gone(escaped_pid)


def test_a_bound_that_cuts_a_utf8_character_reports_the_timeout():
    with pytest.raises(EngineError, match="did not finish within 0.5 seconds"):
        engines.run_bounded_in_own_group(["sh", "-c", r"printf '\303'; sleep 30"], 0.5)


def test_a_bounded_command_keeps_bytes_when_asked():
    done = engines.run_bounded_in_own_group(["printf", "caf\\351"], 5, encoding=None)
    assert done.stdout == b"caf\xe9"


SIGTERM_OUTSIDE_SCRATCH = """
import sys
from quality import engines
engines.run(["sh", "-c", f'sleep 300 & echo $! > "{sys.argv[1]}"; wait'])
"""


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
