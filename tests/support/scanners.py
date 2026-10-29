from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

import pytest

from quality import installation, scanners
from tests.support.advisories import reporting

Locator = Callable[[], str]
OUTPUTS = Path(__file__).parents[1] / "fixtures" / "scanner-outputs"


CLEAN_SCANS = {
    scanners.GITLEAKS: 'while [ $# -gt 0 ]; do [ "$1" = --report-path ] && report=$2; shift; done\necho "[]" > "$report"',
    scanners.ACTIONLINT: "echo '[]'",
    scanners.OSV_SCANNER: reporting(OUTPUTS / "osv-scanner-clean.json", 0),
}


def fake_scanner(directory: Path, name: str, on_scan: str, version: str | None = None) -> Path:
    """Writes a fake `name` into `directory` that prints `version` (the manifest's by default) and runs `on_scan`
    (shell) for any other call."""
    version_arguments = " ".join(scanners.VERSION_ARGUMENTS[name])
    reported = version or scanners.pinned_tool(name).version
    binary = directory / name
    binary.write_text(f'#!/bin/sh\nif [ "$*" = "{version_arguments}" ]; then echo {reported}; exit 0; fi\n{on_scan}\n')
    binary.chmod(0o755)
    return binary


def write_mise(directory: Path, body: str) -> Path:
    """Writes a `mise` that runs `body` (shell) for any call; the arguments of `mise --cd DIR which NAME --tool TOOL`
    are $1 to $6."""
    directory.mkdir(parents=True, exist_ok=True)
    binary = directory / "mise"
    binary.write_text(f"#!/bin/sh\n{body}\n")
    binary.chmod(0o755)
    return binary


def serving(directory: Path) -> str:
    """The body of a mise that has every scanner in `directory`."""
    return f'echo "{directory}/$4"'


NOT_INSTALLED = 'echo "mise ERROR no version is installed" >&2; exit 1'


def lacking(served: Path, *names: str) -> str:
    """The body of a mise that has not installed `names` and serves every other scanner from `served`."""
    return f'case "$4" in {"|".join(names)}) {NOT_INSTALLED};; esac\n{serving(served)}'


def recording(record: Path, body: str) -> str:
    """`body` after a line that appends one line to `record` for each call: the arguments, `|`, MISE_CEILING_PATHS."""
    return f'printf "%s\\n" "$*|$MISE_CEILING_PATHS" >> {record}\n{body}'


def fake_zizmor(directory: Path, on_scan: str, version: str | None = None) -> Locator:
    """Writes a fake zizmor that reports `version` (quality's pin by default) and runs `on_scan` (shell) for any other
    call, and returns the locator that names it."""
    reported = version or installation.required_version(scanners.ZIZMOR)
    version_arguments = " ".join(scanners.VERSION_ARGUMENTS[scanners.ZIZMOR])
    directory.mkdir(parents=True, exist_ok=True)
    binary = directory / scanners.ZIZMOR
    binary.write_text(f'#!/bin/sh\nif [ "$*" = "{version_arguments}" ]; then echo {scanners.ZIZMOR} {reported}; exit 0; fi\n{on_scan}\n')
    binary.chmod(0o755)
    return lambda: str(binary)


def nothing_installed() -> NoReturn:
    """A locator for a scanner that quality's installation does not hold."""
    raise FileNotFoundError


def never_located() -> NoReturn:
    """A locator for a run that must locate nothing."""
    pytest.fail("a scanner was located where nothing needed one")
