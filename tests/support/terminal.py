import contextlib
import io
import os
import re
from pathlib import Path

from quality import cli


class Terminal(io.StringIO):
    """A stream that answers as a terminal does, so a test can read what the door paints."""

    def isatty(self) -> bool:
        return True


def run_on(repo: Path, stdout: io.StringIO, stderr: io.StringIO, environment: dict[str, str]) -> int:
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        return cli.main(["hygiene", str(repo)], clock=lambda: 0.0, environment=environment)


def without_sgr(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def read_pseudo_terminal(parent: int, child: int) -> str:
    """Everything the finished process wrote to the pseudo-terminal, which `parent` and `child` are closed after."""
    os.close(child)
    chunks = []
    with contextlib.suppress(OSError):
        while chunk := os.read(parent, 4096):
            chunks.append(chunk)
    os.close(parent)
    return b"".join(chunks).decode()
