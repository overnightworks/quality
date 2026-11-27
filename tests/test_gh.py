import os

import pytest

from quality.gh import run_gh


@pytest.mark.integration
def test_run_gh_returns_what_gh_printed_and_passes_its_standard_input_on(tmp_path, monkeypatch):
    executable = tmp_path / "gh"
    executable.write_text('#!/bin/sh\necho "args: $*"\ncat\n')
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    assert run_gh(["api", "user"], "body") == "args: api user\nbody"
