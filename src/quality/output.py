"""The one owner of what quality prints and of the step summary; argparse's help, usage errors and `--version`, and
`input()`'s prompt on stdout, are the writers that remain outside it."""

import os
import secrets
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TextIO

from quality.report import Command, Finding, Instruction, OneOf, Step, printable, render


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


class Glyph(StrEnum):
    """What a row's first mark says: nothing to do, needs you, could not check, or a note."""

    CLEAN = "✓"
    NEEDS_YOU = "!"
    COULD_NOT_CHECK = "✗"
    NOTE = "·"


@dataclass(frozen=True)
class SubLine:
    glyph: Glyph
    text: str


@dataclass(frozen=True)
class Row:
    """`glyph subject event fact`, the event a member of an enum the caller owns; with a `count` it reads `3 findings`."""

    glyph: Glyph
    subject: str
    event: StrEnum
    fact: str = ""
    count: int | None = None
    sub_lines: tuple[SubLine, ...] = ()


@dataclass(frozen=True)
class Verdict:
    """The closing line of a run that judged: counts first, the notes of what was only measured, the duration last."""

    findings: int
    areas_with_findings: int
    clean_areas: int
    areas: int
    notes: tuple[str, ...]
    seconds: float


SUBJECT_WIDTH = 12
EVENT_WIDTH = 14
SEPARATOR = " · "
RESET = "\x1b[0m"
BOLD, DIM, RED, GREEN, YELLOW, CYAN = "1", "2", "31", "32", "33", "36"
GLYPH_STYLE = {Glyph.CLEAN: GREEN, Glyph.NEEDS_YOU: YELLOW, Glyph.COULD_NOT_CHECK: RED, Glyph.NOTE: DIM}
VERDICT_STYLE = {Glyph.CLEAN: BOLD, Glyph.NEEDS_YOU: f"{BOLD};{YELLOW}"}
SECONDS_PER_MINUTE = 60


def counted(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def format_duration(seconds: float) -> str:
    """Whole seconds rounded down: `<1s`, `12s`, `1m12s`."""
    whole = int(seconds)
    if whole < 1:
        return "<1s"
    minutes, rest = divmod(whole, SECONDS_PER_MINUTE)
    return f"{minutes}m{rest:02d}s" if minutes else f"{rest}s"


def style_is_on(stream: TextIO | None, environment: Mapping[str, str]) -> bool:
    """Colour only on a terminal that asks for it: `NO_COLOR` unset or empty and `TERM` not `dumb`; a closed or missing
    stream is plain."""
    if stream is None or stream.closed or not stream.isatty():
        return False
    return not environment.get("NO_COLOR") and environment.get("TERM") != "dumb"


def _paint(text: str, style: str, styled: bool) -> str:
    return f"\x1b[{style}m{text}{RESET}" if styled and text else text


def _event_text(row: Row) -> str:
    event = printable(str(row.event))
    return event if row.count is None else counted(row.count, event)


def _row_lines(row: Row, styled: bool) -> list[str]:
    """Each field is escaped and padded before it is painted, so the columns hold at a terminal as they do in a pipe."""
    subject, fact = printable(row.subject).ljust(SUBJECT_WIDTH), printable(row.fact)
    middle = f"{subject}  {_event_text(row).ljust(EVENT_WIDTH)} " if fact else f"{subject}  {_event_text(row)}"
    if row.glyph is Glyph.NOTE:
        lines = [_paint(f"  {row.glyph} {middle}{fact}", DIM, styled)]
    else:
        lines = [f"  {_paint(row.glyph, GLYPH_STYLE[row.glyph], styled)} {middle}{_paint(fact, DIM, styled)}"]
    return lines + [f"    {_paint(sub.glyph, GLYPH_STYLE[sub.glyph], styled)} {printable(sub.text)}" for sub in row.sub_lines]


def _verdict_parts(verdict: Verdict) -> tuple[Glyph, list[str]]:
    if verdict.findings == 0:
        glyph, parts = Glyph.CLEAN, ["clean", counted(verdict.areas, "area")]
    else:
        glyph = Glyph.NEEDS_YOU
        parts = [f"{counted(verdict.findings, 'finding')} in {counted(verdict.areas_with_findings, 'area')}"]
        if verdict.clean_areas:
            parts.append(f"{counted(verdict.clean_areas, 'area')} clean")
    return glyph, [*parts, *verdict.notes, format_duration(verdict.seconds)]


def _stream(file: TextIO | None) -> TextIO | None:
    return sys.stdout if file is None else file


def _print_lines(lines: list[str], file: TextIO | None) -> None:
    for line in lines:
        print(line, file=file)


def write_rows(rows: tuple[Row, ...], file: TextIO | None = None) -> None:
    styled = style_is_on(_stream(file), os.environ)
    _print_lines([line for row in rows for line in _row_lines(row, styled)], file)


def write_verdict(verdict: Verdict, file: TextIO | None = None) -> None:
    styled = style_is_on(_stream(file), os.environ)
    glyph, parts = _verdict_parts(verdict)
    style = VERDICT_STYLE[glyph]
    painted = [_paint(f"{glyph} {parts[0]}", style, styled), *(_paint(part, DIM, styled) for part in parts[1:])]
    _print_lines([SEPARATOR.join(painted)], file)


INDENT = "    "


def _step_lines(step: Step) -> list[tuple[int, str]]:
    """A step as lines, each at a level: a command alone on its line, a label above the commands it applies to."""
    match step:
        case Command(text, None) | Instruction(text):
            return [(0, text)]
        case Command(text, label):
            return [(0, f"{label}:"), (1, text)]
        case OneOf(label, choices):
            return [(0, f"{label}, one of:"), *(line for choice in choices for line in [(1, f"{choice.label}:"), (2, choice.text)])]


def _next_lines(next_steps: tuple[Step, ...], label: str) -> list[str]:
    """One step begins on the `next:` line; several stand below a bare `next:`, four spaces per level."""
    lines = [_step_lines(step) for step in next_steps]
    if len(lines) == 1:
        (_, first), *rest = lines[0]
        return [f"{label} {printable(first)}", *(f"{INDENT * level}{printable(text)}" for level, text in rest)]
    return [label, *(f"{INDENT * (level + 1)}{printable(text)}" for step in lines for level, text in step)]


def write_pair(error: str, next_steps: tuple[Step, ...], file: TextIO | None = None) -> None:
    """`error:` and `next:`, the pair that ends a run that could not judge; stderr by default."""
    stream = sys.stderr if file is None else file
    styled = style_is_on(stream, os.environ)
    _print_lines([f"{_paint('error:', f'{BOLD};{RED}', styled)} {printable(error)}"], stream)
    _print_lines(_next_lines(next_steps, _paint("next:", CYAN, styled)), stream)
