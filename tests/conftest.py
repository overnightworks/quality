import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

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
