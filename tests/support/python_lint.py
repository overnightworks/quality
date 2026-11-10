"""Values and builders that the Python rules' tests share."""

from pathlib import Path

from quality import installation

EVAL = 'eval("1")\n'
EVAL_REASON = "S307 Use of possibly insecure function; consider using `ast.literal_eval`"
PRINT = 'print("x")'
PRINT_REASON = "T201 `print` found"
OWNER = "# ruff: noqa: T201 - the command line owns its output"


def fake_ruff(directory: Path, on_check: str, version: str | None = None, on_version: str = "exit 0") -> Path:
    """A ruff that reports `version`, then runs `on_version` (shell), and runs `on_check` (shell) for any other call."""
    binary = directory / "ruff"
    reported = version or installation.required_version("ruff")
    version_branch = f'if [ "$1" = "--version" ]; then echo "ruff {reported}"; {on_version}; fi'
    binary.write_text(f"#!/bin/sh\n{version_branch}\n{on_check}\n")
    binary.chmod(0o755)
    return binary


def ruff_entry(code: str = "S307", filename: str = "/r/src/a.py", noqa_row: int | None = 1) -> dict:
    return {"code": code, "message": "m", "filename": filename, "location": {"row": 1, "column": 1}, "noqa_row": noqa_row}
