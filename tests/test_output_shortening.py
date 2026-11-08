"""What the printer does with the window of a terminal: free text ends at it with an ellipsis, a count, a measurement or an
outcome never does, and columns hold by display width in every stream."""

import io
import pty
import re
import subprocess
import sys
import termios
from enum import StrEnum

import pytest

from quality import env_file, output
from quality.output import Glyph, Row, SubLine, Verdict
from quality.report import Command, Example, Finding, FindingEvent, Note, NoteEvent
from tests.support.check import refusal_pair, refusal_row
from tests.support.door import CHECK_WORD, MODULE_DOOR, door_environment
from tests.support.python_lint import OWNER, PRINT
from tests.support.terminal import SGR, Terminal, read_pseudo_terminal, run_on, screen, without_sgr


class Event(StrEnum):
    CLEAN = "clean"
    FINDING = "finding"
    WIDE = "漢字漢字"


def at_window(columns: int) -> tuple[Terminal, output.Channel]:
    terminal = Terminal()
    return terminal, output.Channel(terminal, True, columns)


def finding(path: str, event: StrEnum, fact: str, detail: tuple[str, ...] = (), fix: str | Example = "a fix") -> Finding:
    return Finding("some-rule", path, None, fact, fix, detail=detail, event=event)


def note(path: str, event: StrEnum, fact: str) -> Note:
    return Note(path, event, fact)


def lines_at(columns: int, write) -> list[str]:
    """What a writer prints to a terminal of `columns`, after quality's SGR."""
    terminal, channel = at_window(columns)
    write(channel)
    return without_sgr(terminal.getvalue()).splitlines()


def findings_at(columns: int, findings: list[Finding], notes: list[Note] | None = None) -> list[str]:
    return lines_at(columns, lambda channel: output.write_findings_and_notes(findings, notes or [], channel))


def entry_lists(entry: Finding | Note) -> tuple[list[Finding], list[Note]]:
    return ([entry], []) if isinstance(entry, Finding) else ([], [entry])


def rows_at(columns: int, *rows: Row) -> list[str]:
    return lines_at(columns, lambda channel: output.write_rows(rows, channel))


def piped(write) -> list[str]:
    stream = io.StringIO()
    write(output.open_channel(stream, {"TERM": "xterm"}))
    return stream.getvalue().splitlines()


@pytest.mark.parametrize(
    ("fact", "columns", "expected_fact"),
    [
        ("12345678", 30, "12345678"),
        ("123456789", 30, "1234567…"),
        ("漢字漢字漢", 30, "漢字漢…"),
        ("漢字漢字漢", 31, "漢字漢字…"),
        ("é" * 9, 30, "é" * 7 + "…"),
        ("ab\x1b[35mcd", 30, "ab\\x1b[…"),
        ("ab\x1b[35mcd", 27, "ab…"),
        ("ab\u202ecd", 31, "ab\\u202e…"),
        ("a · b · c · d", 30, "a · b ·…"),
        ("src/shop/parse.py", 30, "src/sho…"),
        ("12", 22, "…"),
        ("12", 10, "…"),
        ("1", 22, "…"),
        ("1", 10, "…"),
        ("1", 23, "1"),
        ("", 10, ""),
        ("", 22, ""),
    ],
    ids=[
        "fits exactly",
        "one over",
        "wide, one cell short",
        "wide, filled",
        "a mark stays with its letter",
        "an escape survives whole",
        "an escape that does not fit",
        "a format character",
        "a separator has no rule",
        "a path has no rule",
        "at the prefix",
        "below the prefix",
        "one cell at the prefix",
        "one cell below it",
        "one cell past it",
        "an empty fact below the prefix",
        "an empty fact at the prefix",
    ],
)
def test_a_findings_fact_fills_what_the_window_leaves(fact, columns, expected_fact):
    """LENGTH-2, LENGTH-8, LENGTH-9, LENGTH-10: a free-text fact fills what the window leaves, cut at its end and never
    inside an escape, counted in display width, and `…` alone at or below the text before it."""
    (line, *_) = findings_at(columns, [finding("a.env", FindingEvent.TRACKED, fact)])
    assert line == f"    ! a.env  tracked  {expected_fact}"


@pytest.mark.parametrize(
    ("entry", "line"),
    [
        (
            finding("a.env", FindingEvent.GREW, "function read_rows has 71 lines · ceiling 64"),
            "    ! a.env  grew  function read_rows has 71 lines · ceiling 64",
        ),
        (
            finding("a.env", FindingEvent.CONFLICT, "a and b in c match one advisory · none allows it"),
            "    ! a.env  conflict  a and b in c match one advisory · none allows it",
        ),
        (
            finding("a.env", FindingEvent.SECRET, "gitleaks rule x · value not shown"),
            "    ! a.env  secret  gitleaks rule x · value not shown",
        ),
        (
            note("a.env", NoteEvent.ALLOWED, "an allowance with a reason that runs on and on and on and on"),
            "    · a.env  allowed  an allowance with a reason that runs on and on and on and on",
        ),
        (
            finding("a.env", FindingEvent.TRACKED, "an explanation that goes on for far longer than any window"),
            "    ! a.env  tracked  an expl…",
        ),
        (
            note("a.env", NoteEvent.WARNING, "an explanation that goes on for far longer than any window"),
            "    · a.env  warning  an expl…",
        ),
    ],
    ids=["grew", "conflict", "secret", "allowed", "tracked", "warning"],
)
def test_a_fact_of_counts_or_an_outcome_stands_whole_and_free_text_is_cut(entry, line):
    """LENGTH-2, LENGTH-4: a free-text fact is cut at the window; a count, a measurement and an outcome stand whole."""
    assert findings_at(30, *entry_lists(entry))[0] == line


@pytest.mark.parametrize(
    ("entry", "is_cut"),
    [
        (
            finding("a.env", event, "x" * 60),
            event
            in {
                FindingEvent.TRACKED, FindingEvent.UNREADABLE_PORT, FindingEvent.HOST_NETWORK,
                FindingEvent.CLAUDE_ONLY, FindingEvent.SYMLINK, FindingEvent.DIVERGES,
                FindingEvent.NO_AGENTS_FILE, FindingEvent.MISNAMED, FindingEvent.MISPLACED,
                FindingEvent.NO_HEADER, FindingEvent.STRAY, FindingEvent.INSECURE,
                FindingEvent.PRINT_CALL, FindingEvent.OPEN,
            },
        )
        for event in FindingEvent
    ]
    + [(note("a.env", event, "x" * 60), event in {NoteEvent.WARNING, NoteEvent.OPEN}) for event in NoteEvent],
    ids=[f"finding {event.name}" for event in FindingEvent] + [f"note {event.name}" for event in NoteEvent],
)
def test_only_the_free_text_events_are_cut_at_a_terminal(entry, is_cut):
    """LENGTH-3: exactly the listed events are cut at a terminal, and every other event stands whole."""
    (line, *_) = findings_at(40, *entry_lists(entry))
    assert (line.endswith("…"), "x" * 60 in line) == (is_cut, not is_cut)


def test_a_descriptive_row_and_its_sub_lines_end_at_the_window():
    """LENGTH-1: the reason on a `✗` row and a `✗` line under a row end at the window."""
    reason = Row(Glyph.COULD_NOT_CHECK, "files", Event.CLEAN, "a reason of many words", descriptive=True)
    sub_line = Row(Glyph.COULD_NOT_CHECK, "files", Event.CLEAN, sub_lines=(SubLine(Glyph.COULD_NOT_CHECK, "a long reason"),))
    assert rows_at(40, reason) == ["  ✗ files         clean          a reas…"]
    assert rows_at(15, sub_line)[1] == "    ✗ a long r…"


def test_a_whole_sub_line_and_the_lines_below_it_stand_whole_at_a_terminal():
    """LENGTH-7: a line a row marks whole stands whole at a terminal, and so do the lines below it."""
    whole = Row(Glyph.NEEDS_YOU, "files", Event.CLEAN, sub_lines=(SubLine(Glyph.NEEDS_YOU, "a long reason", whole=True, below=("reason: a long reason",)),))
    assert rows_at(15, whole) == ["  ! files         clean", "    ! a long reason", "        reason: a long reason"]


def test_each_row_keeps_what_it_protects():
    """LENGTH-4: a row's extent and counts stand whole at a terminal."""
    rows = (
        Row(Glyph.CLEAN, "code", Event.CLEAN, "a long extent that goes on"),
        Row(Glyph.NOTE, "debt", Event.CLEAN, "six rules · measured only"),
        Row(Glyph.NEEDS_YOU, "files", Event.FINDING, count=3),
        Row(Glyph.COULD_NOT_CHECK, "files", Event.CLEAN, "2 reasons"),
    )
    assert rows_at(20, *rows) == [
        "  ✓ code          clean          a long extent that goes on",
        "  · debt          clean          six rules · measured only",
        "  ! files         3 findings",
        "  ✗ files         clean          2 reasons",
    ]


@pytest.mark.parametrize(
    ("fix", "fix_lines"),
    [
        (
            "a fix sentence that runs on and on and on and on and past it",
            ["      fix: a fix sentence that runs on and on and on and on and past it"],
        ),
        (Example("copy these lines into the file and run it", ("0" * 64,)), ["      fix: copy these lines into the file and run it:", "        " + "0" * 64]),
    ],
    ids=["a sentence", "lines to copy"],
)
def test_detail_fix_and_lines_to_copy_stand_whole(fix, fix_lines):
    """LENGTH-5, ROW-5, ROW-6: the detail lines behind their marker, the `fix:` line and its lines to copy stand whole,
    deeper under the finding, at a terminal."""
    detail = ("a detail line from another tool that is far longer than the window",)
    printed = findings_at(30, [finding("a.env", FindingEvent.TRACKED, "a fact", detail, fix)])
    assert printed == [
        "    ! a.env  tracked  a fact",
        "      | a detail line from another tool that is far longer than the window",
        *fix_lines,
    ]


def test_the_verdict_and_the_pair_stand_whole():
    """LENGTH-6: the verdict, `error:` and `next:` with its commands and lines to copy stand whole at a terminal."""
    verdict = Verdict(2, 1, 3, 4, ("a note that is long",), 0.0)
    steps = (Command("run this long command", "do this"), Example("copy these", ("0" * 64,)))
    printed = lines_at(10, lambda channel: (output.write_verdict(verdict, channel), output.write_pair("an error far longer than the window", steps, channel)))
    assert printed == [
        "! 2 findings in 1 area · 3 areas clean · a note that is long · <1s",
        "error: an error far longer than the window",
        "next:",
        "    do this:",
        "        run this long command",
        "    copy these:",
        "        " + "0" * 64,
    ]


def test_a_wide_location_narrows_the_fact_by_its_cells():
    """LENGTH-2, LENGTH-10: a wide location narrows the room of a finding's fact by its cells."""
    printed = findings_at(30, [finding("漢字.env", FindingEvent.TRACKED, "a fact that goes on and on")])
    assert printed[0] == "    ! 漢字.env  tracked  a fa…"


def test_a_wide_subject_and_event_narrow_a_rows_fact_by_their_cells():
    """LENGTH-1, LENGTH-10: a wide subject and event narrow the room of a row's reason by their cells."""
    row = Row(Glyph.COULD_NOT_CHECK, "漢字", Event.WIDE, "abcdefghij", descriptive=True)
    assert rows_at(40, row) == ["  ✗ 漢字          漢字漢字       abcdef…"]


def test_a_shortened_fact_is_painted_as_one_span():
    """LENGTH-1, LENGTH-2: a cut fact of a finding, a note and a reason is painted as a whole fact is, `…` inside."""
    styles = Terminal()
    output.write_rows(
        (
            Row(Glyph.NEEDS_YOU, "files", Event.FINDING, "FACT"),
            Row(Glyph.NOTE, "files", Event.CLEAN, "FACT"),
            Row(Glyph.COULD_NOT_CHECK, "files", Event.CLEAN, "FACT", descriptive=True),
        ),
        output.Channel(styles, True, None),
    )
    finding_style, note_style, reason_style = styles.getvalue().splitlines()
    glyph_opening, glyph_closing = re.search(rf"({SGR})!({SGR})", finding_style).groups()
    opening, closing = re.search(rf"({SGR})FACT({SGR})$", finding_style).groups()
    note_opening, note_closing = re.fullmatch(rf"({SGR}).*({SGR})", note_style).groups()
    reason_glyph_opening, reason_glyph_closing = re.search(rf"({SGR})✗({SGR})", reason_style).groups()
    reason_opening, reason_closing = re.search(rf"({SGR})FACT({SGR})$", reason_style).groups()
    explanation = "an explanation that goes on for far longer than any window"
    terminal, channel = at_window(30)
    output.write_findings_and_notes(
        [finding("a.env", FindingEvent.TRACKED, explanation)], [note("a.env", NoteEvent.WARNING, explanation)], channel
    )
    painted_finding, _, painted_note = terminal.getvalue().splitlines()
    assert painted_finding == f"    {glyph_opening}!{glyph_closing} a.env  tracked  {opening}an expl…{closing}"
    assert painted_note == f"{note_opening}    · a.env  warning  an expl…{note_closing}"
    reason_terminal = Terminal()
    reason = Row(Glyph.COULD_NOT_CHECK, "files", Event.CLEAN, "a reason of many words", descriptive=True)
    output.write_rows((reason,), output.Channel(reason_terminal, True, 40))
    expected = f"  {reason_glyph_opening}✗{reason_glyph_closing} files         clean          {reason_opening}a reas…{reason_closing}"
    assert reason_terminal.getvalue().splitlines() == [expected]


@pytest.mark.parametrize(
    ("path", "padding"),
    [("漢字.env", 1), ("Ａ.env", 3), ("é.env", 4), ("a⃝.env", 4)],
    ids=["wide", "fullwidth", "nonspacing mark", "enclosing mark"],
)
def test_a_path_is_padded_by_its_cells(path, padding):
    """ROW-4: a path is padded by its display width: a wide or fullwidth character takes two cells, a mark none."""
    printed = piped(lambda channel: output.write_findings_and_notes([finding(path, FindingEvent.TRACKED, "x"), finding("plain.env", FindingEvent.TRACKED, "x")], [], channel))
    rows = [line for line in printed if line.lstrip().startswith("!")]
    assert rows == [f"    ! {path}{' ' * (padding + 2)}tracked  x", "    ! plain.env  tracked  x"]


def test_the_widest_path_is_measured_in_cells():
    """ROW-4: the longest path of an area is measured in cells, wide characters included."""
    printed = piped(lambda channel: output.write_findings_and_notes([finding("漢字字.env", FindingEvent.TRACKED, "x"), finding("plain.env", FindingEvent.TRACKED, "x")], [], channel))
    rows = [line for line in printed if line.lstrip().startswith("!")]
    assert rows == ["    ! 漢字字.env  tracked  x", "    ! plain.env   tracked  x"]


@pytest.mark.parametrize(
    ("findings", "notes", "rows"),
    [
        (
            [finding("a.env", Event.WIDE, "x"), finding("b.env", FindingEvent.TRACKED, "x")],
            [],
            ["    ! a.env  漢字漢字  x", "    ! b.env  tracked   x"],
        ),
        (
            [],
            [note("a.env", Event.WIDE, "x"), note("b.env", NoteEvent.ALLOWED, "x")],
            ["    · a.env  漢字漢字  x", "    · b.env  allowed   x"],
        ),
    ],
    ids=["finding", "note"],
)
def test_an_event_is_padded_by_its_cells(findings, notes, rows):
    """ROW-4: a finding's and a note's event is padded by its display width."""
    printed = piped(lambda channel: output.write_findings_and_notes(findings, notes, channel))
    assert [line for line in printed if line.lstrip().startswith(("!", "·"))] == rows


def test_a_rows_subject_and_event_are_padded_by_their_cells():
    """ROW-4: a row's subject and event are padded by their display width."""
    subject = Row(Glyph.CLEAN, "漢字", Event.CLEAN, "fact")
    event = Row(Glyph.CLEAN, "files", Event.WIDE, "fact")
    printed = piped(lambda channel: output.write_rows((subject, event), channel))
    assert printed == ["  ✓ 漢字          clean          fact", "  ✓ files         漢字漢字       fact"]


@pytest.mark.parametrize(
    ("environment", "process_columns"),
    [({}, None), ({"COLUMNS": "100"}, None), ({}, "100")],
    ids=["no COLUMNS", "COLUMNS injected", "COLUMNS in the process"],
)
def test_a_terminal_answers_for_its_window(window, monkeypatch, environment, process_columns):
    """LENGTH-11, LENGTH-13: a terminal answers for its window, whatever COLUMNS says."""
    if process_columns:
        monkeypatch.setenv("COLUMNS", process_columns)
    channel = output.open_channel(window(60).stream, {"TERM": "xterm"} | environment)
    assert channel.width == 60


def test_a_window_of_no_columns_is_unknown(window):
    """LENGTH-14: a terminal that answers no columns has no known window."""
    assert output.open_channel(window(0).stream, {"TERM": "xterm"}).width is None


def test_each_stream_reads_its_own_window(window, monkeypatch):
    """LENGTH-11: each stream reads the window of its own terminal."""
    monkeypatch.setattr(sys, "stdout", window(60).stream)
    monkeypatch.setattr(sys, "stderr", window(100).stream)
    channels = output.open_channels({"TERM": "xterm"})
    assert (channels.out.width, channels.err.width) == (60, 100)


@pytest.mark.parametrize(
    ("kind", "environment"),
    [("window", {"NO_COLOR": "1"}), ("window", {"TERM": "dumb"}), ("string", {}), ("terminal", {})],
    ids=["NO_COLOR", "dumb", "a pipe", "a terminal without a descriptor"],
)
def test_a_stream_that_is_not_styled_has_no_width_and_shortens_nothing(window, kind, environment):
    """LENGTH-14: a stream that is not a styled terminal has no width and shortens nothing."""
    pseudo = window(60) if kind == "window" else None
    stream = pseudo.stream if pseudo else {"string": io.StringIO, "terminal": Terminal}[kind]()
    channel = output.open_channel(stream, {"TERM": "xterm"} | environment)
    fact = "an explanation that goes on for far longer than any window"
    output.write_findings_and_notes([finding("a.env", FindingEvent.TRACKED, fact)], [], channel)
    printed = without_sgr(pseudo.read() if pseudo else stream.getvalue())
    assert channel.width is None
    assert f"    ! a.env  tracked  {fact}" in printed.splitlines()


class AskedForNoDescriptor(io.StringIO):
    def fileno(self) -> int:
        raise AssertionError


@pytest.mark.parametrize("closed", [True, False], ids=["closed", "missing"])
def test_a_closed_or_missing_stream_is_never_asked_for_a_width(closed):
    """LENGTH-14: a closed or missing stream is never asked for a width."""
    stream = AskedForNoDescriptor()
    stream.close()
    assert output.open_channel(stream if closed else None, {"TERM": "xterm"}).width is None


def test_the_window_read_at_the_start_holds_for_every_row_after_a_resize(window):
    """LENGTH-12: the window read at the start holds for every row printed after a resize."""
    terminal = window(40)
    channel = output.open_channel(terminal.stream, {"TERM": "xterm"})
    terminal.resize(200)
    cause = Row(
        Glyph.COULD_NOT_CHECK, "files", Event.CLEAN, "a reason of many words",
        sub_lines=(SubLine(Glyph.COULD_NOT_CHECK, "a long reason that goes on and on and on past it"),), descriptive=True,
    )
    explanation = "an explanation that goes on for far longer than any window"
    output.write_rows((cause,), channel)
    output.write_findings_and_notes(
        [finding("a.env", FindingEvent.TRACKED, explanation), finding("b.env", FindingEvent.GREW, "function read_rows has 71 lines · ceiling 64")],
        [note("c.env", NoteEvent.WARNING, explanation), note("d.env", NoteEvent.ALLOWED, explanation)],
        channel,
    )
    output.write_verdict(Verdict(2, 1, 3, 4, ("a note that is long",), 0.0), channel)
    output.write_pair("an error far longer than the window", (Command("run this long command"),), channel)
    assert without_sgr(terminal.read()).splitlines() == [
        "  ✗ files         clean          a reas…",
        "    ✗ a long reason that goes on and on…",
        "    ! a.env  tracked  an explanation th…",
        "    ! b.env  grew     function read_rows has 71 lines · ceiling 64",
        "      fix: a fix",
        "    · c.env  warning  an explanation th…",
        "    · d.env  allowed  " + explanation,
        "! 2 findings in 1 area · 3 areas clean · a note that is long · <1s",
        "error: an error far longer than the window",
        "next: run this long command",
    ]


@pytest.mark.parametrize(
    ("files", "columns", "cuts", "code"),
    [
        ({"src/.env": "A=1\n"}, 60, {"    ! src/.env  tracked": f"    ! src/.env  tracked  {env_file.ENV_FACT[:34]}…"}, 1),
        ({"notes.txt": "x\n"}, 30, {}, 1),
        ({"compose.yaml": "services: [\n"}, 20, {"  ✗ files": f"{refusal_row('files')}…"}, 2),
        (
            {"compose.yaml": "services: [\n", "pyproject.toml": '[project]\nrequires-python = "three"\n'},
            20,
            {"    ✗ cannot read": "    ✗ cannot read c…", "    ✗ pyproject": "    ✗ pyproject.tom…"},
            2,
        ),
        ({"src/a.py": f"{OWNER}\n{PRINT}\n"}, 40, {}, 0),
    ],
    ids=["a tracked file", "an unlisted file", "one reason", "two reasons", "an allowed print"],
)
def test_a_run_cuts_exactly_the_descriptive_facts(build_repo, window, files, columns, cuts, code):
    """LENGTH-1, LENGTH-2, LENGTH-4, LENGTH-6: a run cuts exactly the free-text facts and nothing else, and prints the
    pair whole."""
    repo = build_repo(files)
    plain_out, plain_err = io.StringIO(), io.StringIO()
    run_on(repo, plain_out, plain_err, {"TERM": "xterm"})
    out, err = window(columns), window(columns)
    assert run_on(repo, out.stream, err.stream, {"TERM": "xterm"}) == code
    plain = plain_out.getvalue().splitlines()
    expected = [next((cut for start, cut in cuts.items() if line.startswith(start)), line) for line in plain]
    assert sum(line != cut for line, cut in zip(plain, expected, strict=True)) == len(cuts)
    assert without_sgr(out.read()).splitlines() == expected
    assert screen(err.read()) == (refusal_pair(repo) if code == 2 else "")


@pytest.mark.parametrize(
    ("out_columns", "err_columns", "environment"),
    [(None, 30, {}), (30, 30, {"NO_COLOR": "1"}), (30, 30, {"TERM": "dumb"})],
    ids=["a pipe on stdout", "NO_COLOR", "dumb"],
)
def test_a_door_that_may_not_shorten_prints_the_whole_fact(build_repo, window, out_columns, err_columns, environment):
    """LENGTH-14: a door whose stream is a pipe, `NO_COLOR` or dumb prints the whole fact."""
    repo = build_repo({"src/.env": "A=1\n"})
    out = window(out_columns) if out_columns else None
    err = window(err_columns)
    stdout = out.stream if out else io.StringIO()
    code = run_on(repo, stdout, err.stream, {"TERM": "xterm"} | environment)
    printed = out.read() if out else stdout.getvalue()
    lines = without_sgr(printed).splitlines()
    assert f"    ! src/.env  tracked  {env_file.ENV_FACT}" in lines
    assert ("…" in printed, screen(err.read()), code) == (False, "", 1)


@pytest.mark.integration
@pytest.mark.parametrize("extra_environment", [{}, {"COLUMNS": "30"}], ids=["no COLUMNS", "COLUMNS exported"])
def test_the_real_door_shortens_to_its_pseudo_terminals_window(build_repo, extra_environment):
    """LENGTH-2, LENGTH-11, LENGTH-13: the real door cuts to its pseudo-terminal's window, whatever COLUMNS says."""
    repo = build_repo({"src/.env": "A=1\n"})
    base = door_environment()
    door = [*MODULE_DOOR, CHECK_WORD]
    plain = subprocess.run(door, cwd=repo, env=base, capture_output=True, text=True, check=False)
    parent, child = pty.openpty()
    termios.tcsetwinsize(child, (24, 60))
    done = subprocess.run(door, cwd=repo, env={**base, "TERM": "xterm", **extra_environment}, stdout=child, stderr=subprocess.PIPE, text=True, check=False)
    printed = without_sgr(read_pseudo_terminal(parent, child)).replace("\r\n", "\n").splitlines()
    cut = f"    ! src/.env  tracked  {env_file.ENV_FACT[:34]}…"
    expected = [cut if line.startswith("    ! src/.env  tracked") else line for line in plain.stdout.splitlines()]
    assert (printed[:-1], done.stderr) == (expected[:-1], "")


@pytest.mark.parametrize(
    ("columns", "line"),
    [
        (31, "  ⠋ dependencies  osv-scanner"),
        (30, "  ⠋ dependencies  osv-scanner"),
        (29, "  ⠋ dependencies  osv-scann…"),
        (20, "  ⠋ dependencies  …"),
        (3, " …"),
    ],
    ids=["31 whole", "30 whole", "29", "20", "3"],
)
def test_the_live_line_keeps_the_last_column_of_its_window_free(columns, line):
    terminal, channel = at_window(columns)
    with output.live_line(channel)("dependencies", "osv-scanner"):
        up = without_sgr(terminal.getvalue())
    assert up == f"{line}\r"


def test_the_live_line_is_one_dim_span():
    row_terminal, row_channel = at_window(80)
    output.write_rows((Row(Glyph.NOTE, "debt", Event.CLEAN),), row_channel)
    painted_row = row_terminal.getvalue().splitlines()[0]
    opening, closing = painted_row.split(without_sgr(painted_row))
    terminal, channel = at_window(80)
    with output.live_line(channel)("secrets", "gitleaks"):
        up = terminal.getvalue()
    assert up == f"{opening}  ⠋ secrets       gitleaks{closing}\r"


def test_a_channel_with_no_width_gets_no_line():
    terminal = Terminal()
    with output.live_line(output.Channel(terminal, True, None))("secrets", "gitleaks"):
        pass
    assert terminal.getvalue() == ""


def test_a_failed_erase_leaves_the_interrupt_in_flight(window):
    terminal = window(80)
    channel = output.Channel(terminal.stream, True, 80)
    with pytest.raises(KeyboardInterrupt), output.live_line(channel)("secrets", "gitleaks"):
        terminal.hang_up()
        raise KeyboardInterrupt


def test_an_interrupt_during_the_draw_still_erases_the_part_drawn():
    class Interrupted(Terminal):
        def write(self, text: str) -> int:
            written = super().write(text)
            if not self.getvalue().endswith("\x1b[K"):
                raise KeyboardInterrupt
            return written

    terminal = Interrupted()
    channel = output.Channel(terminal, True, 80)
    with pytest.raises(KeyboardInterrupt), output.live_line(channel)("secrets", "gitleaks"):
        pytest.fail("the block must not run once the draw was interrupted")
    assert without_sgr(terminal.getvalue()) == "  ⠋ secrets       gitleaks\r\r\x1b[K"
