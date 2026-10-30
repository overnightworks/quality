import json
import re
from collections.abc import Sequence
from pathlib import Path

TEST_OWNER = "o"
GITHUB_BASE_VARIABLE = "FAKE_GITHUB_BASE"
# git maps https://github.com/ onto the base before it looks anything up; -c, because the anonymous environment sets
# GIT_CONFIG_COUNT itself.
GITHUB_REDIRECT = f'set -- -c "url.${GITHUB_BASE_VARIABLE}.insteadOf=https://github.com/" "$@"'
NEWEST, OLDER = "b" * 40, "a" * 40


RELEASES = {"v0.9.0": OLDER, "v0.10.0": NEWEST}  # the newest by version, not by text


MATRIX_HEADING = "**Repo × rule, newest release v0.10.0**"


def write_fleet(directory: Path, repositories: Sequence[str]) -> Path:
    """A repository whose quality/fleet.toml lists these entries; json.dumps writes a TOML array of basic strings."""
    (directory / "quality").mkdir(parents=True)
    (directory / "quality" / "fleet.toml").write_text(f"repositories = {json.dumps(repositories)}\n")
    return directory


def row(output: str, name: str, table: int = 0) -> list[str]:
    """The cells of repository `name` in the size table (0) or the matrix (1)."""
    lines = [line for line in output.splitlines() if line.startswith(f"| {name} |")]
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", lines[table].strip("|"))]
