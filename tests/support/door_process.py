"""The door as a process: `cli.run` with its words from argv, judged at `pinned_release`, or, after a leading
`--running <version>:vcs:<commit>` or `--running <version>:dir:<path>`, by `pin.judging_release` as that installation;
a following `--block-ruff <fifo>` makes the ruff lookup write `ready` to the FIFO and block."""

import sys
import threading
from functools import partial
from pathlib import Path

from quality import cli, pin
from quality.installation import DirectoryOrigin, VcsOrigin
from quality.pin import RunningQuality
from tests.support.door import judging_at_the_pin


def running_as(word: str) -> RunningQuality:
    version, _, origin = word.partition(":")
    kind, _, value = origin.partition(":")
    if kind == "vcs":
        return RunningQuality(version, Path(pin.__file__).parent, VcsOrigin(value))
    return RunningQuality(version, Path(value) / "src" / "quality", DirectoryOrigin(Path(value)))


def blocked_ruff(fifo: Path) -> str:
    """Python loses a signal that arrives just before a wait blocks, so the wait is bounded and repeated."""
    fifo.write_text("ready")
    while True:
        threading.Event().wait(0.1)


def ports_and_words(words: list[str]) -> tuple[cli.DoorPorts, list[str]]:
    if words[:1] != ["--running"]:
        return cli.DoorPorts(judging=judging_at_the_pin), words
    judging = partial(pin.judging_release, running=running_as(words[1]))
    if words[2:3] == ["--block-ruff"]:
        return cli.DoorPorts(judging=judging, locate_ruff=partial(blocked_ruff, Path(words[3]))), words[4:]
    return cli.DoorPorts(judging=judging), words[2:]


if __name__ == "__main__":
    ports, words = ports_and_words(sys.argv[1:])
    cli.run(partial(cli.main, words, ports=ports))
