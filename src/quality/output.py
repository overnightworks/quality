"""The one owner of what quality prints, of the step summary and of the step output; argparse's `--version` is the
writer that remains outside it."""

import io
import os
import re
import secrets
import sys
import unicodedata
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass
from enum import StrEnum
from functools import partial
from typing import BinaryIO, TextIO

from quality.report import (
    Choice,
    Command,
    Example,
    Finding,
    FindingEvent,
    Instruction,
    Note,
    NoteEvent,
    OneOf,
    ScannerRun,
    Step,
    printable,
    render,
)

# The name gate.yml reads as `steps.<id>.outputs.scanner-arguments` from the properties step.
SCANNER_ARGUMENTS_OUTPUT = "scanner-arguments"
# The name and value `gate.yml` reads as `steps.preflight.outputs.sonar` to skip the scan and the findings.
SONAR_OUTPUT = "sonar"
SONAR_OFF = "off"
STEP_OUTPUT_VARIABLE = "GITHUB_OUTPUT"
STEP_SUMMARY_VARIABLE = "GITHUB_STEP_SUMMARY"
BACKTICK_RUN = re.compile("`+")
MINIMUM_FENCE = 3


def emit(*lines: str, file: TextIO | None = None) -> None:
    """The one print boundary: every line the door prints leaves through `printable`, and the door never splits text,
    so a line of repository text cannot become several lines."""
    for line in lines:
        print(printable(line), file=file)


def emit_finding(finding: Finding) -> None:
    """A finding's rendered lines; `render` escapes every newline of repository text, so the split finds only its own."""
    emit(*render(finding).split("\n"))


def append_step_summary(lines: Sequence[str], environment: Mapping[str, str]) -> None:
    """In a GitHub Actions step, the run summary gets the same escaped lines as the log, starting on a line of their
    own after whatever an earlier step wrote."""
    summary = environment.get(STEP_SUMMARY_VARIABLE)
    if summary:
        with open(summary, "a+b") as handle:
            start = "" if _ends_a_line(handle) else "\n"
            handle.write((start + "".join(f"{printable(line)}\n" for line in lines)).encode("utf-8"))


def _ends_a_line(handle: BinaryIO) -> bool:
    """Whether the file is empty or its last byte is a line feed."""
    if handle.seek(0, os.SEEK_END) == 0:
        return True
    handle.seek(-1, os.SEEK_END)
    return handle.read(1) == b"\n"


def code_block(lines: Sequence[str]) -> tuple[str, ...]:
    """Lines markdown shows as written: inside one fence longer than any backtick run in them, which CommonMark lets no
    line inside close, so no job name or `what` opens a comment, a link or a heading in the step summary."""
    longest = max((len(run) for line in lines for run in BACKTICK_RUN.findall(line)), default=0)
    fence = "`" * max(MINIMUM_FENCE, longest + 1)
    return (fence, *lines, fence)


def append_step_output(name: str, value: str) -> None:
    """In a GitHub Actions step, later steps read the value as `steps.<id>.outputs.<name>`."""
    step_output = os.environ.get(STEP_OUTPUT_VARIABLE)
    if step_output:
        with open(step_output, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


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
    """What a row's first mark says: nothing to do, needs you, could not check, a note, or what a dry run would change."""

    CLEAN = "✓"
    NEEDS_YOU = "!"
    COULD_NOT_CHECK = "✗"
    NOTE = "·"
    WILL_CHANGE = "~"


@dataclass(frozen=True)
class SubLine:
    """A reason under a row; at a terminal its text is shortened unless it is `whole`, and each of `below` stands beneath
    it, deeper and dim."""

    glyph: Glyph
    text: str
    whole: bool = False
    below: tuple[str, ...] = ()


@dataclass(frozen=True)
class Row:
    """`glyph subject event fact`, the event a member of an enum the caller owns; with a `count` it reads `3 findings`."""

    glyph: Glyph
    subject: str
    event: StrEnum
    fact: str = ""
    count: int | None = None
    sub_lines: tuple[SubLine, ...] = ()
    descriptive: bool = False
    fix: str = ""
    counts_plural: bool = True


@dataclass(frozen=True)
class Verdict:
    """The closing line of a run that judged: counts first, the notes of what was only measured, the duration last."""

    findings: int
    areas_with_findings: int
    clean_areas: int
    areas: int
    notes: tuple[str, ...]
    seconds: float
    ceiling: str = ""
    would_change: bool = False


SUBJECT_WIDTH = 12
EVENT_WIDTH = 14
SEPARATOR = " · "
RESET = "\x1b[0m"
BOLD, DIM, RED, GREEN, YELLOW, CYAN = "1", "2", "31", "32", "33", "36"
GLYPH_STYLE = {Glyph.CLEAN: GREEN, Glyph.NEEDS_YOU: YELLOW, Glyph.COULD_NOT_CHECK: RED, Glyph.NOTE: DIM, Glyph.WILL_CHANGE: CYAN}
VERDICT_STYLE = {Glyph.CLEAN: BOLD, Glyph.NEEDS_YOU: f"{BOLD};{YELLOW}", Glyph.WILL_CHANGE: f"{BOLD};{CYAN}"}
SECONDS_PER_MINUTE = 60
ELLIPSIS = "…"
LIVE_MARK = "⠋"
# A carriage return, then ECMA-48 "erase in line" from the cursor to the row's end.
CLEAR_LINE = "\r\x1b[K"
ZERO_WIDTH_CATEGORIES = frozenset({"Mn", "Me"})
WIDE_CLASSES = frozenset({"W", "F"})
# Free text a terminal may cut; any other event, a new one included, stands whole.
DESCRIPTIVE_FINDINGS = frozenset(
    {
        FindingEvent.TRACKED,
        FindingEvent.UNREADABLE_PORT,
        FindingEvent.HOST_NETWORK,
        FindingEvent.CLAUDE_ONLY,
        FindingEvent.SYMLINK,
        FindingEvent.DIVERGES,
        FindingEvent.NO_AGENTS_FILE,
        FindingEvent.MISNAMED,
        FindingEvent.MISPLACED,
        FindingEvent.NO_HEADER,
        FindingEvent.STRAY,
        FindingEvent.INSECURE,
        FindingEvent.PRINT_CALL,
        FindingEvent.OPEN,
    }
)
DESCRIPTIVE_NOTES = frozenset({NoteEvent.WARNING})
NOTE_GLYPHS = {
    **dict.fromkeys((NoteEvent.LOWERED, NoteEvent.DROPPED, NoteEvent.MOVED, NoteEvent.RECORDED), Glyph.CLEAN),
    **dict.fromkeys((NoteEvent.LOWER, NoteEvent.DROP, NoteEvent.MOVE, NoteEvent.RECORD), Glyph.WILL_CHANGE),
}
DRY_RUN = "dry run"
NOTHING_WRITTEN = "nothing written"


def counted(count: int, noun: str, plural: str | None = None) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {plural or noun + 's'}"


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


@dataclass(frozen=True)
class Channel:
    """One output stream, its style and the columns of its terminal (None where unknown), captured once when the run
    opened it; the writers do not probe it again."""

    stream: TextIO | None
    styled: bool
    width: int | None


@dataclass(frozen=True)
class Channels:
    """The door's two streams, each decided on its own."""

    out: Channel
    err: Channel


def _window_width(stream: TextIO) -> int | None:
    """The columns of `stream`'s terminal, else None, which shortens nothing."""
    try:
        return os.get_terminal_size(stream.fileno()).columns or None
    except OSError:
        return None


def open_channel(stream: TextIO | None, environment: Mapping[str, str]) -> Channel:
    styled = style_is_on(stream, environment)
    return Channel(stream, styled, _window_width(stream) if styled else None)


def open_channels(environment: Mapping[str, str]) -> Channels:
    """Each stream's style, decided once for the run from the environment the door is given; `output.py` reads the
    process environment only for the step output."""
    return Channels(open_channel(sys.stdout, environment), open_channel(sys.stderr, environment))


def _paint(text: str, style: str, styled: bool) -> str:
    return f"\x1b[{style}m{text}{RESET}" if styled and text else text


def _cell_width(character: str) -> int:
    if unicodedata.category(character) in ZERO_WIDTH_CATEGORIES:
        return 0
    return 2 if unicodedata.east_asian_width(character) in WIDE_CLASSES else 1


def _display_width(text: str) -> int:
    return sum(_cell_width(character) for character in text)


def _padded(text: str, width: int) -> str:
    return text + " " * (width - _display_width(text))


def _fitted(fact: str, prefix: str, width: int | None) -> str:
    """`fact` escaped and, where it does not fit after `prefix` in a known `width`, cut at its end with `…`."""
    escaped = printable(fact)
    if width is None or not fact:
        return escaped
    room = width - _display_width(prefix)
    if _display_width(escaped) <= room:
        return escaped
    kept, used = [], 0
    for character in fact:
        rendered = printable(character)
        used += _display_width(rendered)
        if used >= room:
            break
        kept.append(rendered)
    return f"{''.join(kept)}{ELLIPSIS}"


def _event_word(row: Row | Finding | Note) -> str:
    return printable(str(row.event))


def _event_text(row: Row) -> str:
    event = _event_word(row)
    if row.count is None:
        return event
    return counted(row.count, event, None if row.counts_plural else event)


def _row_lines(row: Row, channel: Channel, subject_width: int = SUBJECT_WIDTH) -> list[str]:
    """Each field is escaped and padded before it is painted, so the columns hold at a terminal as they do in a pipe."""
    styled = channel.styled
    subject = _padded(printable(row.subject), subject_width)
    middle = f"{subject}  {_padded(_event_text(row), EVENT_WIDTH)} " if row.fact else f"{subject}  {_event_text(row)}"
    prefix = f"  {row.glyph} {middle}"
    fact = _fitted(row.fact, prefix, channel.width if row.descriptive else None)
    if row.glyph is Glyph.NOTE:
        lines = [_paint(f"{prefix}{fact}", DIM, styled)]
    else:
        lines = [f"  {_paint(row.glyph, GLYPH_STYLE[row.glyph], styled)} {middle}{_paint(fact, DIM, styled)}"]
    for sub in row.sub_lines:
        text = _fitted(sub.text, f"    {sub.glyph} ", None if sub.whole else channel.width)
        lines.append(f"    {_paint(sub.glyph, GLYPH_STYLE[sub.glyph], styled)} {text}")
        lines += [_paint(f"        {printable(line)}", DIM, styled) for line in sub.below]
    if row.fix:
        lines += _fix_lines(row.fix)
    return lines


def _verdict_parts(verdict: Verdict) -> tuple[Glyph, list[str]]:
    ceiling = [part for part in (verdict.ceiling, NOTHING_WRITTEN if verdict.would_change else "") if part]
    if verdict.findings:
        glyph = Glyph.NEEDS_YOU
        parts = [f"{counted(verdict.findings, 'finding')} in {counted(verdict.areas_with_findings, 'area')}"]
        if verdict.clean_areas:
            parts.append(f"{counted(verdict.clean_areas, 'area')} clean")
        parts += ceiling
    elif verdict.would_change:
        glyph, parts = Glyph.WILL_CHANGE, [DRY_RUN, *ceiling]
    else:
        glyph, parts = Glyph.CLEAN, ["clean", *ceiling, counted(verdict.areas, "area")]
    return glyph, [*parts, *verdict.notes, format_duration(verdict.seconds)]


def _print_lines(lines: list[str], channel: Channel) -> None:
    """A closed stream takes nothing: its lines never move to the other stream."""
    stream = channel.stream
    if stream is None:
        return
    try:
        for line in lines:
            print(line, file=stream)
    except BrokenPipeError:
        _discard_further_writes(stream)


def _discard_further_writes(stream: TextIO) -> None:
    """The reader of `stream` left: its descriptor now leads to the null device, so the rest of the run's lines and the
    flush at exit succeed and the run ends with its own exit."""
    with open(os.devnull, "wb") as null_device:
        os.dup2(null_device.fileno(), stream.fileno())


def write_rows(rows: tuple[Row, ...], channel: Channel, subject_width: int = SUBJECT_WIDTH) -> None:
    _print_lines([line for row in rows for line in _row_lines(row, channel, subject_width)], channel)


@dataclass(frozen=True)
class DoorStream:
    """The operator's door's stdout, its one writer. A line it refuses (a full disk) leads it to the null device, so
    it takes nothing more and no later write of the door can stop a request; a closed stream and a reader that left
    take the lines as every stream does. Its channel is private, so no caller reaches the raw stream past it."""

    _channel: Channel

    def show(self, rows: tuple[Row, ...]) -> bool:
        """Whether the stream took `rows`; what that means is the door's to decide."""
        return self._took([line for row in rows for line in _row_lines(row, self._channel)])

    def end_interrupted_line(self) -> None:
        """Ctrl-C leaves the cursor behind the terminal's `^C`, so the door's next line starts on a line of its own."""
        self._took([""])

    def _took(self, lines: list[str]) -> bool:
        stream = self._channel.stream
        if stream is None:
            return True
        try:
            _print_lines(lines, self._channel)
        except OSError:
            _discard_further_writes(stream)
            return False
        return True


def _put(channel: Channel, text: str) -> None:
    stream = channel.stream
    if stream is None:
        return
    try:
        print(text, end="", file=stream, flush=True)
    except OSError:
        # A terminal that hung up must not replace the run's exception or exit: the live line only decorates the run,
        # and a prompt nobody can read is still read: where it shows is the operator's setting.
        _discard_further_writes(stream)


@contextmanager
def _line_up(channel: Channel, subject: str, scanner: str) -> Iterator[None]:
    """One line on `channel` while the block runs, gone on every way out Python still runs. It keeps the window's last
    column free and parks the cursor at the first, so from three columns up neither it nor a key's echo wraps and
    outlives the erase."""
    line = _fitted(f"  {LIVE_MARK} {_padded(subject, SUBJECT_WIDTH)}  {scanner}", " ", channel.width)
    try:
        _put(channel, f"{_paint(line, DIM, channel.styled)}\r")
        yield
    finally:
        _put(channel, CLEAR_LINE)


def live_line(channel: Channel) -> Callable[[str, str], AbstractContextManager[None]]:
    """The line of a stream that knows its window; a line that may wrap could not be erased, so any other stream gets none."""
    if channel.width is None:
        return lambda _subject, _scanner: nullcontext()
    return partial(_line_up, channel)


# A line taken from another tool's rendering carries this, so no line the door prints starts with repository text.
DETAIL_MARKER = "|"
FINDING_INDENT = "    "
FIX_INDENT = "      "
FIX_LABEL = "fix:"
FINDING_GAP = "  "


def _row_columns(location: str, event: str, widths: tuple[int, int]) -> str:
    """The location and event columns of a row, each padded to its width and followed by the gap that opens the fact."""
    location_width, event_width = widths
    return f"{_padded(location, location_width)}{FINDING_GAP}{_padded(event, event_width)}{FINDING_GAP}"


def _detail_line(text: str) -> str:
    """One line of another tool's rendering under its finding's row; a blank line is the bare marker."""
    return f"{FIX_INDENT}{DETAIL_MARKER} {printable(text)}" if text else f"{FIX_INDENT}{DETAIL_MARKER}"


def _fix_lines(fix: str | Example) -> list[str]:
    """A fix as its `fix:` line; a fix with lines to copy ends its label with a colon and stands them two spaces deeper,
    each escaped and none joined."""
    if isinstance(fix, str):
        return [f"{FIX_INDENT}{FIX_LABEL} {printable(fix)}"]
    return [f"{FIX_INDENT}{FIX_LABEL} {printable(fix.label)}:", *(f"{FIX_INDENT}  {printable(line)}" for line in fix.lines)]


def _finding_lines(findings: Sequence[Finding], notes: Sequence[Note], channel: Channel) -> list[str]:
    """Each field is escaped before it is padded, so the columns hold in a pipe as at a terminal; they count display
    width. The findings and the notes share one location column and one event column."""
    styled = channel.styled
    widths = (
        max(_display_width(row.location) for row in (*findings, *notes)),
        max(_display_width(_event_word(row)) for row in (*findings, *notes)),
    )
    lines = []
    for position, finding in enumerate(findings):
        glyph = Glyph.NEEDS_YOU
        columns = _row_columns(finding.location, _event_word(finding), widths)
        width = channel.width if finding.event in DESCRIPTIVE_FINDINGS else None
        fact = _fitted(finding.fact, f"{FINDING_INDENT}{glyph} {columns}", width)
        lines.append(f"{FINDING_INDENT}{_paint(glyph, GLYPH_STYLE[glyph], styled)} {columns}{_paint(fact, DIM, styled)}")
        lines += [_paint(_detail_line(text), DIM, styled) for text in finding.detail]
        if position + 1 == len(findings) or findings[position + 1].fix != finding.fix:
            lines += _fix_lines(finding.fix)
    for note in notes:
        columns = _row_columns(note.location, _event_word(note), widths)
        width = channel.width if note.event in DESCRIPTIVE_NOTES else None
        glyph = NOTE_GLYPHS.get(note.event)
        if glyph:
            fact = _fitted(note.fact, f"{FINDING_INDENT}{glyph} {columns}", width)
            lines.append(f"{FINDING_INDENT}{_paint(glyph, GLYPH_STYLE[glyph], styled)} {columns}{_paint(fact, DIM, styled)}")
            continue
        prefix = f"{FINDING_INDENT}{Glyph.NOTE} {columns}"
        lines.append(_paint(f"{prefix}{_fitted(note.fact, prefix, width)}", DIM, styled))
    return lines


def write_findings_and_notes(findings: Sequence[Finding], notes: Sequence[Note], channel: Channel) -> None:
    """The rows of one area that carry an event: each finding `! location  event  fact` with one `fix:` line after the
    last of each run of equal fixes, then each note `· location  event  fact`, the columns shared."""
    if findings or notes:
        _print_lines(_finding_lines(findings, notes, channel), channel)


def _exit_note(run: ScannerRun) -> Note:
    return Note(run.scanner, NoteEvent.EXITED, SEPARATOR.join(filter(None, (str(run.exit_code), run.withheld))))


def write_scanner_runs(runs: Sequence[ScannerRun], channel: Channel) -> None:
    """Each scanner of an area under `-v`: a `·` row with its exit, then its lines behind the marker, each whole."""
    lines = []
    for run in runs:
        lines += _finding_lines((), (_exit_note(run),), channel)
        lines += [_paint(_detail_line(text), DIM, channel.styled) for text in run.lines]
    _print_lines(lines, channel)


def write_closing_line(glyph: Glyph, parts: Sequence[str], channel: Channel) -> None:
    """The line that ends a run: the glyph and the first part in the glyph's verdict style, each further part dim."""
    styled = channel.styled
    painted = [_paint(f"{glyph} {parts[0]}", VERDICT_STYLE[glyph], styled), *(_paint(part, DIM, styled) for part in parts[1:])]
    _print_lines([SEPARATOR.join(painted)], channel)


def write_lines(lines: Sequence[str], channel: Channel) -> None:
    """Lines that are an artifact, the report's tables: each escaped, never shortened and never painted."""
    _print_lines([printable(line) for line in lines], channel)


def write_verdict(verdict: Verdict, channel: Channel) -> None:
    write_closing_line(*_verdict_parts(verdict), channel)


INDENT = "    "


def _step_lines(step: Step) -> list[tuple[int, str]]:
    """A step as lines, each at a level: a command alone on its line, a label above the commands or the lines to copy it applies to."""
    match step:
        case Command(text, None) | Instruction(text):
            return [(0, text)]
        case Command(text, label):
            return [(0, f"{label}:"), (1, text)]
        case OneOf(label, choices):
            return [(0, f"{label}, one of:"), *(line for choice in choices for line in _choice_lines(choice))]
        case Example(label, lines):
            return [(0, f"{label}:"), *((1, line) for line in lines)]


def _choice_lines(choice: Choice) -> list[tuple[int, str]]:
    """A choice below its `one of:` line: its label above its command."""
    return [(1, f"{choice.label}:"), (2, choice.text)]


def _next_lines(next_steps: tuple[Step, ...], label: str) -> list[str]:
    """One step begins on the `next:` line; several stand below a bare `next:`, four spaces per level."""
    lines = [_step_lines(step) for step in next_steps]
    if len(lines) == 1 and not any(isinstance(step, Command) and step.own_line for step in next_steps):
        (_, first), *rest = lines[0]
        return [f"{label} {printable(first)}", *(f"{INDENT * level}{printable(text)}" for level, text in rest)]
    return [label, *(f"{INDENT * (level + 1)}{printable(text)}" for step in lines for level, text in step)]


def rendered(write: Callable[[Channels], None]) -> tuple[str, ...]:
    """The lines `write` prints, as a pipe shows them: plain, unshortened, stdout's and stderr's in the order written."""
    record = io.StringIO()
    channel = Channel(record, False, None)
    write(Channels(channel, channel))
    return tuple(record.getvalue().splitlines())


def write_help(text: str) -> None:
    """Help reads the same in every stream: plain, whole, on stdout; a closed stdout takes nothing."""
    _print_lines(text.splitlines(), Channel(sys.stdout, False, None))


def write_pair(error: str, next_steps: tuple[Step, ...], channel: Channel) -> None:
    """`error:` and `next:`, the pair that ends a run that could not judge."""
    styled = channel.styled
    _print_lines([f"{_paint('error:', f'{BOLD};{RED}', styled)} {printable(error)}"], channel)
    _print_lines(_next_lines(next_steps, _paint("next:", CYAN, styled)), channel)


def ask(prompt: str, channel: Channel, read_line: Callable[[], str]) -> str | None:
    """The prompt on `channel`, then the line a person ended with Enter, without its line end; None where the input
    ends before Enter, after a line end on `channel`, so the next line starts on its own. The typed line is never
    printed: the terminal's echo is its only copy. Ctrl-C passes through untouched: the shell ends that line."""
    _put(channel, printable(prompt))
    line = read_line()
    if line.endswith("\n"):
        return line.removesuffix("\n")
    _put(channel, "\n")
    return None
