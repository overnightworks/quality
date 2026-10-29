import re
import unicodedata
from collections.abc import Callable
from functools import partial
from importlib.metadata import requires
from pathlib import Path

import pytest

from quality import DISTRIBUTION, cli, installation, python_lint

# Quality's own ruff locator, the composition `cli.main` defaults to.
installed_ruff = partial(installation.installed_executable, python_lint.RUFF_DISTRIBUTION)

# The rules a run without engines judges, in run order; a new rule is added here once.
FILE_RULES = "env-file, compose-port, size, clone, import-contract, claude-md, root-layout, file-suffix, script-name, script-header, test-layout, test-import, python-floor, python-security, python-print"
CLEAN_LINE = f"hygiene: clean ({FILE_RULES})"
ENGINE_RULES = "secret, workflow-lint, workflow-security, advisory"


def requirements_loosening(name: str) -> Callable[[str], list[str]]:
    """A stand-in for `installation.requires` that answers quality's real requirements with `name`'s `==` turned into `>=`."""
    lines = [line.replace("==", ">=") if line.startswith(f"{name}==") else line for line in requires(DISTRIBUTION) or ()]
    return lambda _distribution: lines


def assert_every_line_starts_with_the_door(lines: list[str]) -> None:
    """#97 rule 2, cell 18: no line repository text could forge, and no raw control character between the lines."""
    for line in lines:
        assert not line.lstrip().startswith(("::", "File ", "raise ")), line
        assert all(unicodedata.category(character) != "Cc" for character in line), line


def hygiene_output(
    capsys: pytest.CaptureFixture[str], path: Path, locate_ruff: Callable[[], str] = installed_ruff
) -> tuple[int, str]:
    """The exit code and the whole output of `quality hygiene` over `path`, through the real door."""
    code = cli.main(["hygiene", str(path)], locate_ruff=locate_ruff)
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def compose(*port_lines: str) -> dict[str, str]:
    body = "".join(f"      {line}\n" for line in port_lines)
    return {"compose.yaml": f"services:\n  web:\n    image: x\n    ports:\n{body}"}


def assert_refusal(output: str, rule: str, path: str) -> None:
    assert re.search(rf"^{rule} {re.escape(path)} - \S.*\n  Fix: \S", output, re.MULTILINE), output
