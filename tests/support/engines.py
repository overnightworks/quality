from pathlib import Path

from quality import engines

OUTPUTS = Path(__file__).parents[1] / "fixtures" / "engine-outputs"


def fake_engine(directory: Path, name: str, on_scan: str) -> Path:
    """Writes a fake `name` engine that prints its pinned version and runs `on_scan` (shell) for any other call."""
    version_arguments = " ".join(engines.VERSION_ARGUMENTS[name])
    pinned = engines.read_pins()[name].version
    binary = directory / name
    binary.write_text(f'#!/bin/sh\nif [ "$*" = "{version_arguments}" ]; then echo {pinned}; exit 0; fi\n{on_scan}\n')
    binary.chmod(0o755)
    return binary


def osv_engines(clean_engines, on_scan):
    fake_engine(clean_engines, engines.OSV_SCANNER, on_scan)
    return clean_engines
