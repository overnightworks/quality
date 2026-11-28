import os

import pytest

from quality import gh
from quality.gh import run_gh


@pytest.mark.integration
def test_run_gh_returns_what_gh_printed_and_passes_its_standard_input_on(tmp_path, monkeypatch):
    executable = tmp_path / "gh"
    executable.write_text('#!/bin/sh\necho "args: $*"\ncat\n')
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    assert run_gh(["api", "user"], "body") == "args: api user\nbody"


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ({"GH_CONFIG_DIR": "/g", "XDG_CONFIG_HOME": "/x", "HOME": "/h"}, "/g"),
        ({"XDG_CONFIG_HOME": "/x", "HOME": "/h"}, "/x/gh"),
        ({"HOME": "/h"}, "/h/.config/gh"),
    ],
    ids=["GH_CONFIG_DIR", "XDG_CONFIG_HOME", "home"],
)
def test_gh_keeps_the_login_it_would_use_without_the_changed_home(environment, expected):
    """MERGE-95: gh keeps the login it would use without the changed home: GH_CONFIG_DIR, else XDG_CONFIG_HOME/gh, else
    the home's."""
    assert gh.git_access(environment).settings == {"GH_CONFIG_DIR": expected}
