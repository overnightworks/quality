"""engines.run and engines.git: bytes on request, one failure path, and no child outliving its command."""

import os

import pytest
from conftest import process_is_gone

from quality import engines
from quality.engines import EngineError


def test_git_keeps_bytes_when_asked_and_reads_as_text_otherwise():
    assert engines.git(["--version"], "no git", encoding=None).startswith(b"git version")
    assert engines.git(["--version"], "no git").startswith("git version")


@pytest.mark.parametrize("encoding", ["utf-8", None])
def test_a_failing_git_is_an_engine_error_of_the_failure_and_its_own_words(tmp_path, encoding):
    with pytest.raises(EngineError, match=r"^cannot read: .*not a git repository"):
        engines.git(["-C", str(tmp_path), "rev-parse", "HEAD"], "cannot read", {"PATH": os.environ["PATH"]}, encoding=encoding)


def test_a_command_that_runs_out_of_time_takes_its_children_with_it(tmp_path):
    child = tmp_path / "child-pid"
    with pytest.raises(EngineError, match="sh did not finish within 0.5 seconds"):
        engines.run(["sh", "-c", f'sleep 300 & echo $! > "{child}"; wait'], timeout=0.5)
    assert process_is_gone(int(child.read_text()))
