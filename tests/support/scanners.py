from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

import pytest

from quality import installation, scanners

Locator = Callable[[], str]
OUTPUTS = Path(__file__).parents[1] / "fixtures" / "scanner-outputs"


def fake_engine(directory: Path, name: str, on_scan: str) -> Path:
    """Writes a fake `name` engine that prints its pinned version and runs `on_scan` (shell) for any other call."""
    version_arguments = " ".join(scanners.VERSION_ARGUMENTS[name])
    pinned = scanners.read_pins()[name].version
    binary = directory / name
    binary.write_text(f'#!/bin/sh\nif [ "$*" = "{version_arguments}" ]; then echo {pinned}; exit 0; fi\n{on_scan}\n')
    binary.chmod(0o755)
    return binary


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


def osv_engines(clean_engines, on_scan):
    fake_engine(clean_engines, scanners.OSV_SCANNER, on_scan)
    return clean_engines
