"""`quality merge` with several names as a process, each child replaced by `merge_run_child.py`: `<name> ...` are the
names to merge, and the real `process.run_to_its_end` waits for each child, as it does for the door's own."""

import sys
from functools import partial
from pathlib import Path

from quality import cli, process

CHILD = Path(__file__).with_name("merge_run_child.py")


def start_child(command: list[str]) -> process.Finished:
    """The child that stands for the door of the name `command` merges, with the flags it was given."""
    return process.run_to_its_end([sys.executable, "-P", str(CHILD), *command[command.index("merge") + 1 :]])


if __name__ == "__main__":
    cli.run(partial(cli.main, ["merge", *sys.argv[1:]], ports=cli.DoorPorts(start_child=start_child)))
