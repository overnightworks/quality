import contextlib
import io
import os
import re
import termios
from pathlib import Path
from typing import TextIO

from quality import cli


class Terminal(io.StringIO):
    """A stream that answers as a terminal does, so a test can read what the door paints."""

    def isatty(self) -> bool:
        return True


def run_on(repo: Path, stdout: TextIO, stderr: TextIO, environment: dict[str, str]) -> int:
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        return cli.main(["hygiene", str(repo)], clock=lambda: 0.0, environment=environment)


SGR = r"\x1b\[[0-9;]*m"


def without_sgr(text: str) -> str:
    return re.sub(SGR, "", text)


def _drain(parent: int) -> str:
    chunks = []
    with contextlib.suppress(OSError):
        while chunk := os.read(parent, 4096):
            chunks.append(chunk)
    return b"".join(chunks).decode()


def read_pseudo_terminal(parent: int, child: int) -> str:
    """Everything the finished process wrote to the pseudo-terminal, which `parent` and `child` are closed after."""
    os.close(child)
    text = _drain(parent)
    os.close(parent)
    return text


class PseudoTerminal:
    """A pseudo-terminal of a given width. Its stream owns the child end and `read` owns the parent end; each closes
    its descriptor once, and `close` closes whatever is still open."""

    def __init__(self, columns: int, rows: int = 24) -> None:
        parent, child = os.openpty()
        self._rows = rows
        termios.tcsetwinsize(child, (rows, columns))
        self.stream = os.fdopen(child, "w", encoding="utf-8")
        self._parent: int | None = parent

    def resize(self, columns: int) -> None:
        termios.tcsetwinsize(self.stream.fileno(), (self._rows, columns))

    def read(self) -> str:
        """Everything written to the stream so far, with line feeds for the terminal's CR LF; the stream is closed."""
        self.stream.flush()
        self.stream.close()
        text = _drain(self._parent)
        self._close_parent()
        return text.replace("\r\n", "\n")

    def close(self) -> None:
        self.stream.close()
        self._close_parent()

    def _close_parent(self) -> None:
        if self._parent is not None:
            os.close(self._parent)
            self._parent = None
