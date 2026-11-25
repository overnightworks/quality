"""A process that runs with a pseudo-terminal as its stdin, stdout and stderr, so its terminal checks pass, a typed
line reaches it and Ctrl-C reaches every process of its group, as at a person's terminal."""

import codecs
import contextlib
import os
import select
import signal
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Self

PATIENCE_SECONDS = 60
CONTROL_C = "\x03"
# The new session leader takes the pseudo-terminal as its controlling terminal, then becomes the program.
TAKE_THE_TERMINAL = "import fcntl, os, sys, termios; fcntl.ioctl(0, termios.TIOCSCTTY, 0); os.execv(sys.executable, [sys.executable, *sys.argv[1:]])"


class PtySession:
    """Runs `python <arguments>` in a session of its own; leaving the block kills whatever is still running."""

    def __init__(self, arguments: Sequence[str], environment: Mapping[str, str], cwd: Path) -> None:
        self.master, slave = os.openpty()
        self.output = ""
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.process = subprocess.Popen(
            [sys.executable, "-c", TAKE_THE_TERMINAL, *arguments], stdin=slave, stdout=slave, stderr=slave, env=dict(environment), cwd=cwd,
            start_new_session=True,
        )
        os.close(slave)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(self.process.pid, signal.SIGKILL)
        self.process.wait()
        os.close(self.master)

    def type(self, text: str) -> None:
        os.write(self.master, text.encode())

    def interrupt(self) -> None:
        self.type(CONTROL_C)

    def read_until(self, text: str) -> str:
        """What the process printed so far, once it has printed `text`; the wait is bounded, not a pause."""
        while text not in self.output:
            if not self._read_more():
                raise AssertionError(f"the process ended or stalled before printing {text!r}: {self.output!r}")
        return self.output

    def finish(self) -> tuple[int, str]:
        """The exit and everything the process printed, once it has ended."""
        while self._read_more():
            pass
        return self.process.wait(timeout=PATIENCE_SECONDS), self.output.replace("\r\n", "\n")

    def _read_more(self) -> bool:
        ready, _, _ = select.select([self.master], [], [], PATIENCE_SECONDS)
        if not ready:
            return False
        try:
            chunk = os.read(self.master, 4096)
        except OSError:  # the other end closed: the pseudo-terminal's end of file
            return False
        self.output += self._decoder.decode(chunk)
        return bool(chunk)
