import re
import unicodedata
from collections.abc import Callable
from importlib.metadata import requires
from pathlib import Path

import pytest

from quality import DISTRIBUTION, cli

installed_ruff = cli.INSTALLED_RUFF

# The rules a run without engines judges, in run order; a new rule is added here once.
FILE_RULES = "env-file, compose-port, size, clone, import-contract, claude-md, root-layout, file-suffix, script-name, script-header, test-layout, test-import, python-floor, python-security, python-print"
CLEAN_LINE = f"hygiene: clean ({FILE_RULES})"
ENGINE_RULES = "secret, workflow-lint, workflow-security, advisory"


def loosen_requirement(site: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """Puts temporary metadata for quality first on the search path, its real requirements with `name`'s `==` turned into `>=`."""
    lines = [line.replace("==", ">=") if line.startswith(f"{name}==") else line for line in requires(DISTRIBUTION) or ()]
    dist_info = site / f"{DISTRIBUTION.replace('-', '_')}-0.dist-info"
    dist_info.mkdir(parents=True)
    requirements = "".join(f"Requires-Dist: {line}\n" for line in lines)
    (dist_info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {DISTRIBUTION}\nVersion: 0\n{requirements}")
    monkeypatch.syspath_prepend(str(site))


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
