"""The door as a process that judges at `pinned_release`: `cli.run` with the test door's release, its words from argv."""

from functools import partial

from quality import cli
from tests.support.door import judging_at_the_pin

if __name__ == "__main__":
    cli.run(partial(cli.main, check_ports=cli.CheckPorts(judging=judging_at_the_pin)))
