"""How a test drives `quality merge` with several names: in this process against a fake child port and a fake terminal,
with the rows and pairs the run prints named once here."""

import io
import signal
import sys
from collections.abc import Mapping, Sequence

from quality import cli
from quality.merge_run import run_merge_names
from quality.output import Channel, Channels
from quality.process import Finished
from tests.support.merge import frozen_now

FIRST, SECOND, THIRD = "acme/widgets#1", "acme/widgets#2", "acme/gadgets#3"
SIGKILL_RETURNCODE = -9


class FakeChildren:
    """The port that starts a child: it records each command, answers the ending scripted for the name that command
    merges (exit 0 where none is), and keeps the order in which children started, exited and the terminal was cleared."""

    def __init__(self, endings: Mapping[str, Finished | int] | None = None) -> None:
        self.endings = endings or {}
        self.commands: list[list[str]] = []
        self.events: list[str] = []

    def __call__(self, command: Sequence[str]) -> Finished:
        name = command[command.index("merge") + 1]
        self.commands.append(list(command))
        self.events.append(f"start {name}")
        ending = self.endings.get(name, 0)
        self.events.append(f"exit {name}")
        return ending if isinstance(ending, Finished) else Finished(ending, interrupted=False)

    def discard_input(self) -> None:
        self.events.append("discard input")

    @property
    def names(self) -> list[str]:
        return [command[command.index("merge") + 1] for command in self.commands]


def merge_names(capsys, *words: str, endings: Mapping[str, Finished | int] | None = None, terminal: bool = True):
    """The exit, stdout and stderr of `quality merge <words>` and the children it started, on the fake port."""
    children = FakeChildren(endings)
    standard_input = cli.Terminal(lambda: "", lambda: terminal, children.discard_input)
    code = cli.main(["merge", *words], terminal=standard_input, ports=cli.DoorPorts(start_child=children), clock=lambda: 0.0, now=frozen_now)
    streams = capsys.readouterr()
    return code, streams.out, streams.err, children


class InterruptingStream(io.StringIO):
    """An output stream that takes a Ctrl-C the first time `marker` is written to it, as one that arrives while that text
    is printed."""

    def __init__(self, marker: str | None = None) -> None:
        super().__init__()
        self.marker = marker

    def write(self, text: str) -> int:
        written = super().write(text)
        if self.marker is not None and self.marker in text:
            self.marker = None
            signal.raise_signal(signal.SIGINT)
        return written


def merge_names_interrupted(*words: str, out_marker: str | None = None, err_marker: str | None = None, endings: Mapping[str, Finished | int] | None = None):
    """The exit, stdout and stderr of `quality merge <words>` on the fake child port, with a Ctrl-C arriving while the run
    prints `out_marker` to stdout or `err_marker` to stderr."""
    children = FakeChildren(endings)
    out, err = InterruptingStream(out_marker), InterruptingStream(err_marker)
    code = run_merge_names(
        [cli.named_pull_request(word) for word in words], dry_run=False, verbose=False, is_terminal=True,
        channels=Channels(Channel(out, False, None), Channel(err, False, None)), clock=lambda: 0.0, start_child=children,
        discard_input=children.discard_input,
    )
    return code, out.getvalue(), err.getvalue(), children


def row(glyph: str, subject: str, event: str, fact: str = "", width: int = 12) -> str:
    """One row as the door prints it: the glyph, the subject padded to `width`, the event padded to a column."""
    middle = f"{subject:<{width}}  {event:<14} {fact}" if fact else f"{subject:<{width}}  {event}"
    return f"  {glyph} {middle}\n"


def judging(position: int, total: int, name: str) -> str:
    return row("·", f"{position} of {total}", "judging", name)


def command_of(name: str, *flags: str) -> list[str]:
    """The command that runs the door for `name` alone, as the run starts it."""
    return [sys.executable, "-P", "-m", "quality.cli", "merge", name, *flags]
