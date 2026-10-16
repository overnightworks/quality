import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from quality import cli

FAKE_KEY = "AKIA" + "QYLPMTYX3QZ5ABCD"  # assembled so quality's own scan stays green
RepoBuilder = Callable[[dict[str, str]], Path]


@pytest.fixture
def build_repo(tmp_path: Path) -> RepoBuilder:
    def build(files: dict[str, str]) -> Path:
        git = ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
        subprocess.run([*git, "init", "-q"], check=True)
        for name, content in files.items():
            target = tmp_path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        subprocess.run([*git, "add", "-A", "-f"], check=True)
        subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "fixture"], check=True)
        return tmp_path

    return build


@pytest.fixture(scope="session")
def installed_engines(tmp_path_factory) -> Path:
    directory = tmp_path_factory.mktemp("engines")
    assert cli.main(["engines", "install", str(directory)]) == 0
    return directory


@pytest.fixture
def fake_git(tmp_path, monkeypatch):
    """Puts a `git` first on PATH that runs `preamble` (shell) and then the real git."""
    real_git = shutil.which("git")

    def install(preamble: str) -> None:
        fake_bin = tmp_path / "fake-git"
        fake_bin.mkdir()
        (fake_bin / "git").write_text(f'#!/bin/sh\n{preamble}\nexec {real_git} "$@"\n')
        (fake_bin / "git").chmod(0o755)
        monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")

    return install
