"""The one owner of what quality prints and of the step summary; argparse's help, usage errors and `--version`, and
`input()`'s prompt on stdout, are the writers that remain outside it."""

import os
import secrets
import sys
from collections.abc import Mapping
from typing import TextIO

from quality.report import Finding, printable, render


def emit(*lines: str, file: TextIO | None = None) -> None:
    """The one print boundary: every line the door prints leaves through `printable`, and the door never splits text,
    so a line of repository text cannot become several lines."""
    for line in lines:
        print(printable(line), file=file)


def emit_finding(finding: Finding) -> None:
    """A finding's rendered lines; `render` escapes every newline of repository text, so the split finds only its own."""
    emit(*render(finding).split("\n"))


def append_step_summary(report: tuple[str, ...]) -> None:
    """In a GitHub Actions step, the run summary gets the same escaped lines as the log."""
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("".join(f"{printable(line)}\n" for line in report))


def stop_workflow_commands() -> None:
    """Repository text can spell a workflow command (`::warning::`, `##[add-mask]`), and the runner would run it.

    The stop lasts the rest of the step: the runner keeps the pause per step (actions/runner v2.338.0, `Handler.cs`),
    and the door never prints the line that lifts it. The runner reads the two pipes without order and handles stderr
    first (`ProcessInvoker.cs`), so each stream starts with the stop. The token is new per run and appears only in this
    line, which the runner masks.
    """
    stop = f"::stop-commands::{secrets.token_hex(16)}"
    for stream in (sys.stderr, sys.stdout):
        if stream is not None:
            print(stop, file=stream, flush=True)


def set_up_streams(environment: Mapping[str, str]) -> None:
    # stderr is line-buffered since Python 3.9, but a pipe makes stdout block-buffered, so `2>&1` showed stderr first.
    # stdout is None when fd 1 was closed before Python started.
    if sys.stdout is not None:
        sys.stdout.reconfigure(line_buffering=True)
    if environment.get("GITHUB_ACTIONS") == "true":
        stop_workflow_commands()
