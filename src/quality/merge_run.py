"""`quality merge` with several names: each pull request runs the door in a process of its own, one after the other,
and the run ends with a row per name and one closing line or pair."""

import signal
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from quality.check import DRY_RUN_WORD
from quality.merge import (
    EXIT_COULD_NOT_DO,
    EXIT_REFUSED,
    EXIT_YES,
    GATE_PROPERTY,
    NOTHING_CHANGED,
    DoorEvent,
    refuse_without_terminal,
    restore_command,
)
from quality.output import (
    DRY_RUN,
    SEPARATOR,
    Channels,
    Glyph,
    Row,
    flush_streams,
    format_duration,
    subject_width,
    write_closing_line,
    write_pair,
    write_rows,
)
from quality.process import Finished
from quality.report import Command, Instruction, Step
from quality.verdict import MERGE_COMMAND

EXIT_INTERRUPTED = 128 + signal.SIGINT
# Ordered by what the operator must hear first: a later exit beats an earlier one, so 2 beats 130 beats 1 beats 0.
EXIT_PRECEDENCE = (EXIT_YES, EXIT_REFUSED, EXIT_INTERRUPTED, EXIT_COULD_NOT_DO)
EXIT_OF_EVENT = {
    DoorEvent.NOT_MERGED: EXIT_REFUSED,
    DoorEvent.INTERRUPTED: EXIT_INTERRUPTED,
    DoorEvent.STOPPED: EXIT_COULD_NOT_DO,
    DoorEvent.KILLED: EXIT_COULD_NOT_DO,
}
GLYPH_OF_EVENT = {
    DoorEvent.MERGED: Glyph.CLEAN,
    DoorEvent.WOULD_MERGE: Glyph.WILL_CHANGE,
    DoorEvent.NOT_MERGED: Glyph.NEEDS_YOU,
    DoorEvent.INTERRUPTED: Glyph.NEEDS_YOU,
    DoorEvent.STOPPED: Glyph.COULD_NOT_CHECK,
    DoorEvent.KILLED: Glyph.COULD_NOT_CHECK,
    DoorEvent.NOT_RUN: Glyph.NOTE,
}
# After one of these no later child starts: the run cannot tell whether the gate is still off, or it was interrupted.
STOPPING_EVENTS = frozenset({DoorEvent.STOPPED, DoorEvent.KILLED, DoorEvent.INTERRUPTED})
MERGED_EVENTS = frozenset({DoorEvent.MERGED, DoorEvent.WOULD_MERGE})
INTERRUPTED_ENDS = frozenset({-signal.SIGINT, EXIT_INTERRUPTED})

StartChild = Callable[[Sequence[str]], Finished]


@dataclass(frozen=True)
class PullRequestName:
    """The pull request `quality merge` names: its repository as `<owner>/<repo>` and its number."""

    repository: str
    number: int

    def __str__(self) -> str:
        return f"{self.repository}#{self.number}"


@dataclass(frozen=True)
class Result:
    """What a name came to: the event its summary row says, and the fact after it."""

    name: PullRequestName
    event: DoorEvent
    fact: str = ""


def child_command(name: PullRequestName, *, dry_run: bool, verbose: bool) -> list[str]:
    """The door for `name` alone, in a process of its own. `-P` keeps the working directory off `sys.path`: a pull
    request's checkout may hold a `quality` package, which would run under the operator's admin login."""
    flags = [*([DRY_RUN_WORD] if dry_run else []), *(["-v"] if verbose else [])]
    return [sys.executable, "-P", "-m", "quality.cli", "merge", str(name), *flags]


def result_of(name: PullRequestName, returncode: int, dry_run: bool) -> Result:
    if returncode == EXIT_YES:
        return Result(name, DoorEvent.WOULD_MERGE if dry_run else DoorEvent.MERGED)
    if returncode == EXIT_REFUSED:
        return Result(name, DoorEvent.NOT_MERGED)
    if returncode in INTERRUPTED_ENDS:
        return Result(name, DoorEvent.INTERRUPTED)
    if returncode < 0:
        return Result(name, DoorEvent.KILLED, f"signal {-returncode}")
    return Result(name, DoorEvent.STOPPED)


def summary_row(result: Result) -> Row:
    return Row(GLYPH_OF_EVENT[result.event], str(result.name), result.event, result.fact)


def run_exit(results: Sequence[Result], interrupted: bool) -> int:
    exits = [EXIT_OF_EVENT.get(result.event, EXIT_YES) for result in results]
    return max([*exits, *([EXIT_INTERRUPTED] if interrupted else [])], key=EXIT_PRECEDENCE.index)


def rerun(names: Sequence[PullRequestName], dry_run: bool) -> Command:
    return Command(" ".join([MERGE_COMMAND, *map(str, names), *([DRY_RUN_WORD] if dry_run else [])]))


def merged_count(results: Sequence[Result], total: int, dry_run: bool) -> str:
    merged = sum(result.event in MERGED_EVENTS for result in results)
    return f"{'would merge' if dry_run else 'merged'} {merged} of {total}"


def stopped_pair(last: Result, count: str, rest: Sequence[PullRequestName], dry_run: bool) -> tuple[str, tuple[Step, ...]]:
    """The child that stopped the run, then the names not run: its own step first, to be done at once."""
    if last.event is DoorEvent.KILLED:
        facts = [f"{GATE_PROPERTY} MAY STILL BE OFF on {last.name.repository}", f"{last.name} was killed by {last.fact}"]
        steps: list[Step] = [restore_command(last.name.repository)]
    else:
        facts = [f"stopped at {last.name}"]
        steps = [Instruction(f"do the step named above for {last.name} at once")]
    if rest:
        facts.extend([count, f"{len(rest)} not run"])
        steps.append(rerun(rest, dry_run))
    else:
        facts.append(count)
    return SEPARATOR.join(facts), tuple(steps)


def close_run(code: int, results: Sequence[Result], names: Sequence[PullRequestName], dry_run: bool, seconds: float, channels: Channels) -> None:
    """The one closing line or pair: none where the run was interrupted, as a run for one name leaves none."""
    count = merged_count(results, len(names), dry_run)
    if code == EXIT_YES and dry_run:
        write_closing_line(Glyph.WILL_CHANGE, (DRY_RUN, count, NOTHING_CHANGED), channels.out)
    elif code == EXIT_YES:
        write_closing_line(Glyph.CLEAN, (count, format_duration(seconds)), channels.out)
    elif code == EXIT_REFUSED:
        refused = [result.name for result in results if result.event is DoorEvent.NOT_MERGED]
        write_pair(SEPARATOR.join((count, f"{len(refused)} not merged", NOTHING_CHANGED)), (rerun(refused, dry_run),), channels.err)
    elif code == EXIT_COULD_NOT_DO:
        write_pair(*stopped_pair(results[-1], count, names[len(results) :], dry_run), channels.err)


def run_merge_names(
    names: Sequence[PullRequestName],
    *,
    dry_run: bool,
    verbose: bool,
    is_terminal: bool,
    channels: Channels,
    clock: Callable[[], float],
    start_child: StartChild,
    discard_input: Callable[[], None],
) -> int:
    """Each name's door in its own process, in the order named; the next starts after the previous one exited. The run
    stops where a child's exit cannot be read as nothing changed or is an interruption, and never ends 0 or 1 after one."""
    if not is_terminal:
        return refuse_without_terminal(channels)
    started = clock()
    results: list[Result] = []
    interrupted = False
    for position, name in enumerate(names, start=1):
        write_rows((Row(Glyph.NOTE, f"{position} of {len(names)}", DoorEvent.JUDGING, str(name)),), channels.out)
        flush_streams()
        discard_input()
        finished = start_child(child_command(name, dry_run=dry_run, verbose=verbose))
        results.append(result_of(name, finished.returncode, dry_run))
        interrupted = finished.interrupted
        if interrupted or results[-1].event in STOPPING_EVENTS:
            break
    rows = tuple(summary_row(result) for result in [*results, *(Result(name, DoorEvent.NOT_RUN) for name in names[len(results) :])])
    write_rows(rows, channels.out, subject_width(rows))
    code = run_exit(results, interrupted)
    close_run(code, results, names, dry_run, clock() - started, channels)
    return code
