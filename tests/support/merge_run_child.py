"""A stand-in for the door that `quality merge` starts for one pull request, run as a script: `<name> [flags]`.

`MERGE_CHILD_DIRECTORY` names a directory holding `plan.json`, which maps each name to what that child does:
`exit:<n>`; `wait` (print ready, block until the test writes the FIFO `release`, exit 0); `trap` (the same with SIGINT
caught and named); `kill` (print ready, die by SIGKILL); `ask` (print the question, read one line from stdin and keep it).
The child writes `started-<name>` first, so a test can tell a child that never started.
"""
# ruff: noqa: T201 - this stand-in child prints its progress lines as its one output

import json
import os
import signal
import sys
from pathlib import Path


def file_name(name: str) -> str:
    return name.replace("/", "_").replace("#", "-")


def block_until_released(directory: Path) -> None:
    with open(directory / "release") as released:
        released.read()


def main() -> int:
    name = sys.argv[1]
    directory = Path(os.environ["MERGE_CHILD_DIRECTORY"])
    mode = json.loads((directory / "plan.json").read_text())[name]
    (directory / f"started-{file_name(name)}").write_text(" ".join(sys.argv[1:]))
    if mode.startswith("exit:"):
        return int(mode.removeprefix("exit:"))
    if mode == "trap":
        signal.signal(signal.SIGINT, lambda *_: print(f"child {name} trapped", flush=True))
    if mode in ("wait", "trap"):
        print(f"child {name} ready", flush=True)
        block_until_released(directory)
        (directory / f"finished-{file_name(name)}").write_text("done")
        return 0
    if mode == "kill":
        print(f"child {name} ready", flush=True)
        os.kill(os.getpid(), signal.SIGKILL)
    print(f"child {name} asking", flush=True)
    (directory / f"answer-{file_name(name)}").write_text(sys.stdin.readline())
    print(f"child {name} answered", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
