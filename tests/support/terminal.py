import contextlib
import io
import os
import re
import termios
from pathlib import Path
from typing import TextIO

from quality.output import RESET
from tests.support.door import check_door


class Terminal(io.StringIO):
    """A stream that answers as a terminal does, so a test can read what the door paints."""

    def isatty(self) -> bool:
        return True


def run_on(repo: Path, stdout: TextIO, stderr: TextIO, environment: dict[str, str]) -> int:
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        return check_door(repo, clock=lambda: 0.0, environment=environment)


SGR = r"\x1b\[[0-9;]*m"


def without_sgr(text: str) -> str:
    return re.sub(SGR, "", text)


def screen(text: str) -> str:
    """What a terminal shows of `text` once its escape sequences have acted: a carriage return goes back to the first
    column, erasing to the row's end clears it and a character overwrites the one under it."""
    rows: list[str] = [""]
    column = 0
    for token in re.split(r"(\r|\n|\x1b\[K)", without_sgr(text)):
        if token == "\n":
            rows.append("")
            column = 0
        elif token == "\r":
            column = 0
        elif token == "\x1b[K":
            rows[-1] = rows[-1][:column]
        else:
            rows[-1] = rows[-1][:column] + token + rows[-1][column + len(token) :]
            column += len(token)
    return "\n".join(rows)


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

    def hang_up(self) -> None:
        """Closes the parent end alone, so the next write to the stream fails as a terminal that hung up does."""
        self._close_parent()

    def close(self) -> None:
        self.stream.close()
        self._close_parent()

    def _close_parent(self) -> None:
        if self._parent is not None:
            os.close(self._parent)
            self._parent = None


def by_role(frame: str, glyph: str) -> str:
    """`frame` with each escape sequence named for its role, read off the frame: the one before the first `glyph` is the
    glyph's, the same foreground in bold is the verdict's, a reset is a reset and any other is another."""
    foreground = re.search(rf"({SGR}){re.escape(glyph)}", frame)
    assert foreground, "no painted glyph in the frame"
    roles = {foreground[1]: "{glyph}", f"\x1b[1;{foreground[1][2:]}": "{verdict}", RESET: "{reset}"}
    return re.sub(SGR, lambda sequence: roles.get(sequence[0], "{other}"), frame)
