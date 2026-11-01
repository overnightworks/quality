import contextlib
import io
import os
import re
import subprocess
import sys
import textwrap
import unicodedata
from enum import StrEnum
from pathlib import Path

import pytest

from quality import cli, output, sonar
from quality.output import Glyph, Row, SubLine, Verdict
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
    printable,
)
from tests.support.check import (
    CopyLine,
    FindingLine,
    FixLine,
    assert_block_order,
    decode_block,
    finding_rows,
    note_rows,
)

OWN_SGR = re.compile(r"\x1b\[[0-9;]*m")


class Event(StrEnum):
    CLEAN = "clean"
    FINDING = "finding"


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


def plain(rows: tuple[Row, ...]) -> str:
    stream = io.StringIO()
    output.write_rows(rows, file=stream)
    return stream.getvalue()


def verdict_line(**counts) -> str:
    stream = io.StringIO()
    defaults = {"findings": 0, "areas_with_findings": 0, "clean_areas": 7, "areas": 7, "notes": (), "seconds": 0}
    output.write_verdict(Verdict(**{**defaults, **counts}), file=stream)
    return stream.getvalue()


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (Row(Glyph.CLEAN, "dependencies", Event.CLEAN, "1 lockfile"), "  ✓ dependencies  clean          1 lockfile\n"),
        (Row(Glyph.CLEAN, "files", Event.CLEAN), "  ✓ files         clean\n"),
        (Row(Glyph.NEEDS_YOU, "files", Event.FINDING, count=1), "  ! files         1 finding\n"),
        (Row(Glyph.NEEDS_YOU, "code", Event.FINDING, "see below", count=3), "  ! code          3 findings     see below\n"),
        (Row(Glyph.NOTE, "debt", Event.CLEAN, "measured only"), "  · debt          clean          measured only\n"),
    ],
    ids=["fact", "empty fact is stripped", "one", "several", "note"],
)
def test_a_row_stands_in_the_pictures_columns(row, expected):
    assert plain((row,)) == expected


def test_sub_lines_stand_under_their_row_with_their_own_glyph():
    row = Row(Glyph.COULD_NOT_CHECK, "files", Event.CLEAN, "2 reasons", sub_lines=(SubLine(Glyph.COULD_NOT_CHECK, "first"), SubLine(Glyph.COULD_NOT_CHECK, "second")))
    assert plain((row,)).splitlines()[1:] == ["    ✗ first", "    ✗ second"]


def test_repository_text_in_a_row_is_escaped_before_it_is_padded():
    row = Row(Glyph.CLEAN, "fi\nles", Event.CLEAN, "a\x1b[35mb")
    text = plain((row,))
    assert text == "  ✓ fi\\x0ales     clean          a\\x1b[35mb\n"


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "<1s"), (0.99, "<1s"), (1, "1s"), (59.9, "59s"), (60, "1m00s"), (72, "1m12s")],
)
def test_a_duration_is_whole_seconds_rounded_down(seconds, expected):
    assert output.format_duration(seconds) == expected


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        ({}, "✓ clean · 7 areas · <1s\n"),
        ({"findings": 1, "areas_with_findings": 1, "clean_areas": 6}, "! 1 finding in 1 area · 6 areas clean · <1s\n"),
        ({"findings": 5, "areas_with_findings": 3, "clean_areas": 4, "seconds": 14}, "! 5 findings in 3 areas · 4 areas clean · 14s\n"),
        ({"findings": 2, "areas_with_findings": 2, "clean_areas": 0, "seconds": 72}, "! 2 findings in 2 areas · 1m12s\n"),
        ({"notes": ("debt measured, not enforced",), "seconds": 2}, "✓ clean · 7 areas · debt measured, not enforced · 2s\n"),
        (
            {"findings": 2, "areas_with_findings": 2, "clean_areas": 4, "notes": ("debt partly measured, not enforced",)},
            "! 2 findings in 2 areas · 4 areas clean · debt partly measured, not enforced · <1s\n",
        ),
    ],
    ids=["clean", "one finding", "several", "no clean area is left out", "measured only", "partly measured with findings"],
)
def test_the_verdict_line_counts_and_ends_with_the_duration(counts, expected):
    assert verdict_line(**counts) == expected


TWO_WAYS = OneOf("pick one", (Choice("first command", "first case"), Choice("second command", "second case")))
COPY = Example("write it in a.toml like this", ("[[table]]", 'key = "value"', "other = 1"))


@pytest.mark.parametrize(
    ("steps", "expected"),
    [
        ((Command("do this"),), "next: do this\n"),
        ((Command("do this"), Instruction("then think")), "next:\n    do this\n    then think\n"),
        ((Command("do this", "where"),), "next: where:\n    do this\n"),
        ((TWO_WAYS,), "next: pick one, one of:\n    first case:\n        first command\n    second case:\n        second command\n"),
        ((COPY,), 'next: write it in a.toml like this:\n    [[table]]\n    key = "value"\n    other = 1\n'),
        ((Command("do this"), COPY),
         'next:\n    do this\n    write it in a.toml like this:\n        [[table]]\n        key = "value"\n        other = 1\n'),
        (
            (Command("do this"), TWO_WAYS),
            ("next:\n    do this\n    pick one, one of:\n        first case:\n            first command\n"
             "        second case:\n            second command\n"),
        ),
    ],
    ids=["one step", "two steps", "labelled command", "choice alone", "example alone", "example after a command", "choice after a command"],
)
def test_the_pair_prints_a_lone_step_on_the_next_line_and_several_below_it(steps, expected):
    stream = io.StringIO()
    output.write_pair("e", steps, file=stream)
    assert stream.getvalue() == f"error: e\n{expected}"


def test_the_pair_labels_and_escapes_each_text():
    stream = io.StringIO()
    output.write_pair("a\x1b[35mb", (Command("c\nd"), Instruction("e\x1b")), file=stream)
    assert stream.getvalue() == "error: a\\x1b[35mb\nnext:\n    c\\x0ad\n    e\\x1b\n"


@pytest.mark.parametrize("closed", [False, True], ids=["open", "closed"])
@pytest.mark.parametrize("terminal", [False, True], ids=["pipe", "terminal"])
@pytest.mark.parametrize("no_color", [None, "", "1"], ids=["unset", "empty", "set"])
@pytest.mark.parametrize("term", ["xterm", "dumb"])
def test_only_a_terminal_without_no_color_and_not_dumb_is_styled(terminal, no_color, term, closed):
    stream = Terminal() if terminal else io.StringIO()
    if closed:
        stream.close()
    environment = {"TERM": term} | ({} if no_color is None else {"NO_COLOR": no_color})
    expected = terminal and not closed and no_color in (None, "") and term != "dumb"
    assert output.style_is_on(stream, environment) is expected


def test_a_missing_stream_is_plain():
    assert output.style_is_on(None, {"TERM": "xterm"}) is False


@pytest.fixture
def at_a_terminal(monkeypatch):
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.delenv("NO_COLOR", raising=False)


def written(stream_kind: type[io.StringIO], write) -> str:
    stream = stream_kind()
    write(stream)
    return stream.getvalue()


def test_styled_rows_hold_exactly_the_plain_words(at_a_terminal):
    rows = (
        Row(Glyph.CLEAN, "files", Event.CLEAN, "ok"),
        Row(Glyph.NEEDS_YOU, "code", Event.FINDING, count=2, sub_lines=(SubLine(Glyph.COULD_NOT_CHECK, "why"),)),
        Row(Glyph.NOTE, "debt", Event.CLEAN, "measured only"),
    )
    styled = written(Terminal, lambda stream: output.write_rows(rows, file=stream))
    assert "\x1b[" in styled
    assert OWN_SGR.sub("", styled) == plain(rows)


def test_a_styled_verdict_and_pair_hold_exactly_the_plain_words(at_a_terminal):
    verdict = Verdict(1, 1, 6, 7, ("debt measured, not enforced",), 3)
    assert OWN_SGR.sub("", written(Terminal, lambda stream: output.write_verdict(verdict, file=stream))) == verdict_line(
        findings=1, areas_with_findings=1, clean_areas=6, notes=("debt measured, not enforced",), seconds=3
    )
    pair = written(Terminal, lambda stream: output.write_pair("e", (Command("n"),), file=stream))
    assert ("\x1b[" in pair, OWN_SGR.sub("", pair)) == (True, "error: e\nnext: n\n")


def test_repository_text_reaches_a_terminal_only_as_escaped_text(at_a_terminal):
    row = Row(Glyph.COULD_NOT_CHECK, "files", Event.CLEAN, "\x1b[35m\u202e")
    styled = written(Terminal, lambda stream: output.write_rows((row,), file=stream))
    remaining = OWN_SGR.sub("", styled)
    assert "\\x1b[35m\\u202e" in remaining
    assert [character for character in remaining if unicodedata.category(character) in ("Cc", "Cf", "Zl", "Zp") and character != "\n"] == []


@pytest.mark.parametrize("no_color", ["1", None], ids=["no color", "pipe"])
def test_plain_output_holds_no_escape_byte(monkeypatch, no_color):
    monkeypatch.setenv("TERM", "xterm")
    stream = Terminal() if no_color else io.StringIO()
    if no_color:
        monkeypatch.setenv("NO_COLOR", no_color)
    output.write_rows((Row(Glyph.CLEAN, "files", Event.CLEAN),), file=stream)
    output.write_pair("e", (Command("n"),), file=stream)
    assert "\x1b" not in stream.getvalue()


def finding(path: str, event: FindingEvent, fact: str = "a fact", fix: str = "a fix", line: int | None = None) -> Finding:
    return Finding("some-rule", path, line, fact, fix, event=event)


def note(path: str, fact: str = "a note") -> Note:
    return Note(path, NoteEvent.ALLOWED, fact)


def findings_written(findings: list[Finding], stream_kind: type[io.StringIO] = io.StringIO, notes: list[Note] | None = None) -> str:
    return written(stream_kind, lambda stream: output.write_findings_and_notes(findings, notes or [], file=stream))


def area_row_indent() -> int:
    row = plain((Row(Glyph.NEEDS_YOU, "files", Event.FINDING, count=1),))
    return len(row) - len(row.lstrip(" "))


def test_a_finding_prints_its_row_then_its_fix_deeper_than_an_area_row():
    (row,) = finding_rows(findings_written([finding("a.env", FindingEvent.TRACKED, "the fact", "the fix")]), "a.env")
    assert (row.event, row.fact, row.fix, row.fix_follows) == (FindingEvent.TRACKED, "the fact", "the fix", True)
    assert area_row_indent() < row.indent < row.fix_indent


def test_the_event_and_the_fact_start_at_one_column_after_the_longest_location_and_event():
    findings = [
        finding("a.env", FindingEvent.TRACKED, "first fact", "one"),
        finding("deploy/longer.env", FindingEvent.PUBLISHED_PORT, "second fact", "two"),
        finding("mid.env", FindingEvent.UNUSED, "third fact", "three"),
    ]
    rows = [line for line in findings_written(findings).splitlines() if line.lstrip().startswith("!")]
    event_columns = {line.index(finding.event.value) for line, finding in zip(rows, findings, strict=True)}
    fact_columns = {line.index(finding.reason) for line, finding in zip(rows, findings, strict=True)}
    longest_location = max(findings, key=lambda candidate: len(candidate.location)).location
    longest_event = max(findings, key=lambda candidate: len(candidate.event)).event
    assert (len(event_columns), len(fact_columns)) == (1, 1)
    assert rows[1].index(longest_location) + len(longest_location) < event_columns.pop()
    assert rows[1].index(longest_event) + len(longest_event) < fact_columns.pop()


@pytest.mark.parametrize(
    ("fixes", "fix_follows"),
    [
        ("AA", [False, True]),
        ("ABA", [True, True, True]),
        ("AABB", [False, True, False, True]),
    ],
    ids=["equal pair", "alternating", "two runs"],
)
def test_one_fix_follows_the_last_finding_of_each_run_of_equal_fixes(fixes, fix_follows):
    findings = [finding(f"{number}.env", FindingEvent.TRACKED, fix=f"fix {fix}") for number, fix in enumerate(fixes)]
    printed = findings_written(findings)
    rows = [row for number in range(len(fixes)) for row in finding_rows(printed, f"{number}.env")]
    assert [row.fix_follows for row in rows] == fix_follows
    assert [row.fix for row in rows] == [f"fix {fix}" for fix in fixes]
    assert printed.count(f"{output.FIX_LABEL} ") == len(fix_follows) - fix_follows.count(False)


def test_an_event_findings_detail_prints_escaped_under_its_row_before_the_fix(at_a_terminal):
    detail = ("one\n::error::d", "", "  two\x1b")
    found = Finding("some-rule", "a.env", None, "a fact", "a fix", detail=detail, event=FindingEvent.BROKEN)
    printed = findings_written([found])
    block = decode_block(printed.splitlines())
    (row,) = [line for line in block if isinstance(line, FindingLine)]
    assert row.row.detail == tuple(printable(line) for line in detail)
    assert (row.row.fix, row.row.fix_follows) == ("a fix", True)
    assert f"{output.FIX_INDENT}{output.DETAIL_MARKER}" in printed.splitlines()
    assert_block_order(block)
    assert OWN_SGR.sub("", findings_written([found], Terminal)) == printed


ENTRY = Example("copy this", ("[[table]]", 'key = "esc\x1b"'))


def test_a_fix_with_lines_to_copy_prints_its_label_then_each_line_deeper_and_escaped():
    found = finding("a.env", FindingEvent.SECRET, fix=ENTRY)
    block = decode_block(findings_written([found]).splitlines())
    (row,) = [line for line in block if isinstance(line, FindingLine)]
    copied = [line for line in block if isinstance(line, CopyLine)]
    (fix_line,) = [line for line in block if isinstance(line, FixLine)]
    assert (row.row.fix, row.row.copy) == (ENTRY.label, tuple(printable(text) for text in ENTRY.lines))
    assert len({len(line.text) - len(line.copy) for line in copied}) == 1
    assert len(copied[0].text) - len(copied[0].copy) > len(fix_line.text) - len(fix_line.text.lstrip())
    assert_block_order(block)


def test_two_findings_with_equal_lines_to_copy_print_them_once():
    printed = findings_written([finding(f"{number}.env", FindingEvent.SECRET, fix=ENTRY) for number in range(2)])
    block = decode_block(printed.splitlines())
    assert (sum(isinstance(line, FixLine) for line in block), sum(isinstance(line, CopyLine) for line in block)) == (1, len(ENTRY.lines))


def test_at_a_terminal_the_fix_and_its_lines_to_copy_stay_plain_and_the_words_equal_the_plain_ones(at_a_terminal):
    findings = [finding("a.env", FindingEvent.SECRET, fix=ENTRY)]
    styled = findings_written(findings, Terminal)
    block = decode_block(styled.splitlines())
    assert [line.text for line in block if isinstance(line, FixLine | CopyLine)] == [
        line for line in findings_written(findings).splitlines() if line.lstrip().startswith(ENTRY.label) or line.strip() in map(printable, ENTRY.lines)
    ]
    assert OWN_SGR.sub("", styled) == findings_written(findings)


def test_a_finding_with_a_line_shows_its_location():
    findings = [finding("a.env", FindingEvent.TRACKED, line=7), finding("b.env", FindingEvent.TRACKED)]
    assert [finding_rows(findings_written(findings), candidate.location) != [] for candidate in findings] == [True, True]
    assert findings[0].location != findings[0].path


def test_repository_text_is_escaped_before_it_is_padded():
    hostile = "x\nesc\x1b[31m\u202eend\u2028"
    findings = [finding(f"{hostile}.env", FindingEvent.TRACKED, hostile, hostile), finding("short", FindingEvent.TRACKED, "plain", hostile)]
    printed = findings_written(findings)
    (row,) = finding_rows(printed, findings[0].location)
    assert (row.fact, row.fix) == (printable(hostile), printable(hostile))
    assert [character for character in printed if unicodedata.category(character) in ("Cc", "Cf", "Zl", "Zp") and character != "\n"] == []
    short_row = next(line for line in printed.splitlines() if " short " in line)
    assert short_row.index(FindingEvent.TRACKED.value) == next(line for line in printed.splitlines() if "x\\x0a" in line).index(FindingEvent.TRACKED.value)


def test_wide_and_combining_characters_print_whole():
    paths = ["漢字.env", "e\u0301.env", "plain.env"]
    findings = [finding(path, FindingEvent.TRACKED, f"fact of {path}", f"fix of {path}") for path in paths]
    printed = findings_written(findings)
    rows = [row for path in paths for row in finding_rows(printed, path)]
    assert [(row.fact, row.fix) for row in rows] == [(f"fact of {path}", f"fix of {path}") for path in paths]


@pytest.fixture
def painted_like_an_area_row(at_a_terminal) -> tuple[str, str, str, str]:
    """The paint an area row puts around its `!` and its fact, read from `write_rows` on a terminal."""
    styled = written(Terminal, lambda stream: output.write_rows((Row(Glyph.NEEDS_YOU, "files", Event.FINDING, "FACT"),), file=stream))
    glyph = re.search(r"(\x1b\[[\d;]*m)!(\x1b\[[\d;]*m)", styled)
    fact = re.search(r"(\x1b\[[\d;]*m)FACT(\x1b\[[\d;]*m)", styled)
    return glyph.group(1), glyph.group(2), fact.group(1), fact.group(2)


def test_at_a_terminal_the_glyph_and_the_fact_are_painted_as_an_area_row_paints_them(painted_like_an_area_row):
    glyph_start, glyph_end, fact_start, fact_end = painted_like_an_area_row
    findings = [finding("a.env", FindingEvent.TRACKED, "FACT", "the fix")]
    styled = findings_written(findings, Terminal)
    row_line = next(line for line in styled.splitlines() if "!" in line)
    assert f"{glyph_start}!{glyph_end}" in row_line
    assert f"{fact_start}FACT{fact_end}" in row_line
    unpainted = row_line.replace(f"{glyph_start}!{glyph_end}", "").replace(f"{fact_start}FACT{fact_end}", "")
    assert "\x1b" not in unpainted
    assert "\x1b" not in next(line for line in styled.splitlines() if "the fix" in line)
    assert OWN_SGR.sub("", styled) == findings_written(findings)


@pytest.mark.parametrize(("term", "no_color"), [("xterm", "1"), ("dumb", None)], ids=["NO_COLOR", "dumb"])
def test_a_terminal_that_asks_for_no_style_prints_no_escape_byte(monkeypatch, term, no_color):
    monkeypatch.setenv("TERM", term)
    monkeypatch.delenv("NO_COLOR", raising=False)
    if no_color:
        monkeypatch.setenv("NO_COLOR", no_color)
    assert "\x1b" not in findings_written([finding("a.env", FindingEvent.TRACKED)], Terminal)


def test_no_findings_and_no_notes_print_nothing():
    assert findings_written([]) == ""


@pytest.mark.parametrize(
    ("event", "longer_than_a_notes"),
    [(FindingEvent.UNUSED, False), (FindingEvent.PUBLISHED_PORT, True)],
    ids=["event shorter than a note's", "event longer than a note's"],
)
def test_findings_and_notes_share_one_location_column_and_one_event_column(event, longer_than_a_notes):
    assert (len(event.value) > len(NoteEvent.ALLOWED.value)) == longer_than_a_notes
    findings = [finding("a.env", event, "first fact")]
    notes = [note("deploy/longer.toml", "second fact")]
    printed = findings_written(findings, notes=notes)
    finding_line, note_line = (next(line for line in printed.splitlines() if marker in line) for marker in ("first fact", "second fact"))
    assert finding_line.index("first fact") == note_line.index("second fact")
    assert finding_line.index(event.value) == note_line.index(NoteEvent.ALLOWED.value)
    assert finding_line.index(event.value) > len(notes[0].location)


def test_a_note_row_follows_the_last_fix_and_has_no_fix_of_its_own():
    printed = findings_written([finding("a.env", FindingEvent.TRACKED)], notes=[note("b.toml"), note("c.toml")])
    lines = printed.splitlines()
    note_numbers = [number for number, line in enumerate(lines) if line.lstrip().startswith(Glyph.NOTE)]
    assert note_numbers == [len(lines) - 2, len(lines) - 1]
    assert printed.count(f"{output.FIX_LABEL} ") == 1
    assert [row.event for path in ("b.toml", "c.toml") for row in note_rows(printed, path)] == [NoteEvent.ALLOWED] * 2


def test_notes_alone_print_their_rows_without_a_fix():
    printed = findings_written([], notes=[note("b.toml")])
    assert (len(printed.splitlines()), output.FIX_LABEL in printed) == (1, False)


def test_a_note_row_is_painted_dim_as_a_whole_as_an_area_note_row_is(at_a_terminal):
    area = written(Terminal, lambda stream: output.write_rows((Row(Glyph.NOTE, "debt", Event.CLEAN, "FACT"),), file=stream))
    dim = re.match(r"(\x1b\[[\d;]*m)", area).group(1)
    styled = findings_written([], Terminal, notes=[note("b.toml", "FACT")])
    assert (styled.startswith(dim), styled.rstrip("\n").endswith(output.RESET), OWN_SGR.sub("", styled)) == (True, True, findings_written([], notes=[note("b.toml", "FACT")]))


def test_a_notes_location_and_fact_are_escaped_before_they_are_padded():
    hostile = "x\nesc\x1b[31m\u202eend\u2028"
    printed = findings_written([], notes=[note(hostile, hostile), note("short")])
    (row,) = note_rows(printed, printable(hostile))
    assert row.fact == printable(hostile)
    assert [character for character in printed if unicodedata.category(character) in ("Cc", "Cf", "Zl", "Zp") and character != "\n"] == []
    short_row = next(line for line in printed.splitlines() if " short " in line)
    assert short_row.index(NoteEvent.ALLOWED.value) == next(line for line in printed.splitlines() if "x\\x0a" in line).index(NoteEvent.ALLOWED.value)


ENV_ROW = f"{Glyph.NEEDS_YOU} .env "
FIX_LINE = f"{output.FIX_LABEL} "
MODULE_DOOR = [sys.executable, "-m", "quality.cli"]
CONSOLE_SCRIPT_DOOR = [str(Path(sys.executable).parent / "quality")]


def test_counted_gives_the_noun_for_one_and_its_plural_for_any_other_count():
    assert [output.counted(count, "entry", "entries") for count in (1, 2)] == ["1 entry", "2 entries"]


def quality_door(*arguments: str, cwd: Path, door: list[str] = MODULE_DOOR, **streams) -> subprocess.CompletedProcess[str]:
    return subprocess.run([*door, *arguments], cwd=cwd, text=True, check=False, **streams)


@pytest.fixture
def env_and_broken_compose(build_repo) -> Path:
    return build_repo({".env": "A=1\n", "compose.yaml": "services: [\n"})


@pytest.mark.parametrize("door", [MODULE_DOOR, CONSOLE_SCRIPT_DOOR], ids=["module", "console script"])
def test_a_merged_pipe_shows_the_hygiene_lines_in_run_order(env_and_broken_compose, door):
    """#86 rule 3, cells 10 and 12a: the notes, the area rows with their findings (`.env` as env-file's and root-layout's rows, each with its fix), then the pair."""
    done = quality_door(
        "hygiene", ".", cwd=env_and_broken_compose, door=door, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    prefixes = [
        "workflow-security runs offline", "  ✗ files", ENV_ROW, FIX_LINE, ENV_ROW, FIX_LINE,
        "  · debt", "  · imports", "  ✓ secrets", "  ✓ workflows", "  ✓ dependencies", "  ✓ code", "error: ", "next: ",
    ]
    lines = done.stdout.splitlines()
    indented = (ENV_ROW, FIX_LINE)
    assert all((line.lstrip() if prefix in indented else line).startswith(prefix) for line, prefix in zip(lines, prefixes, strict=True)), done.stdout


def test_discarding_stderr_hides_exactly_the_pair(env_and_broken_compose):
    """#86 rule 3, cell 11: no line changes its stream; the exit stays 2."""
    merged = quality_door("hygiene", ".", cwd=env_and_broken_compose, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    done = quality_door("hygiene", ".", cwd=env_and_broken_compose, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert (done.returncode, merged.stdout.startswith(done.stdout), merged.stdout[len(done.stdout):].count("\n")) == (2, True, 2)


def test_discarding_stdout_leaves_exactly_the_pair(env_and_broken_compose):
    """#86 rule 3: the rows are results and the pair is not."""
    done = quality_door("hygiene", ".", cwd=env_and_broken_compose, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    assert (done.returncode, [line.split(" ", 1)[0] for line in done.stderr.splitlines()]) == (2, ["error:", "next:"])


SONAR_FINDINGS_SCRIPT = """
import sys
sys.path.insert(0, {repository_root!r})
from tests.support.sonar import issues_page, sonar_answering
from quality import cli
cli.run(lambda: cli.main(["sonar", "findings"], transport=sonar_answering(issues=issues_page(2, 1, 2)), sleep=lambda seconds: None))
"""


@pytest.mark.integration
def test_a_merged_pipe_lists_the_sonar_findings_before_the_red_summary(tmp_path):
    """#86 rule 3, cell 12: the findings precede the summary through `2>&1`."""
    from tests.support.sonar import PULL_REQUEST_ENVIRONMENT, TASK_FIXTURE, legacy_config_absent

    (tmp_path / "quality").mkdir()
    (tmp_path / "quality" / "config.toml").write_text(legacy_config_absent)
    (tmp_path / sonar.TASK_FILE).parent.mkdir()
    (tmp_path / sonar.TASK_FILE).write_text(TASK_FIXTURE.read_text())
    script = tmp_path / "findings.py"
    script.write_text(textwrap.dedent(SONAR_FINDINGS_SCRIPT).format(repository_root=str(Path(__file__).parents[1])))
    done = subprocess.run(
        [sys.executable, str(script)], cwd=tmp_path, text=True, check=False, env={**os.environ, **PULL_REQUEST_ENVIRONMENT},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    prefixes = ["sonar src/a1.py:1 - ", "  Fix: ", "sonar src/a2.py:2 - ", "  Fix: ", "sonar: new 2"]
    lines = done.stdout.splitlines()
    assert (done.returncode, all(line.startswith(prefix) for line, prefix in zip(lines, prefixes, strict=True))) == (1, True), done.stdout


def test_a_replaced_stdout_still_prints_the_help():
    """#86 rule 3: `main` leaves the process's streams alone, so a captured stdout works."""
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured), pytest.raises(SystemExit) as stopped:
        cli.main(["--help"])
    assert (stopped.value.code, "hygiene" in captured.getvalue()) == (0, True)


@pytest.mark.integration
def test_a_closed_stdout_object_still_prints_the_pair_on_stderr(tmp_path, capsys, monkeypatch):
    """#86 rule 3: a closed stdout object never crashes `main`; the diagnostic reaches stderr."""
    monkeypatch.setenv("NO_COLOR", "1")  # Python 3.14's argparse otherwise probes the closed stdout for colour
    closed = io.TextIOWrapper(io.BytesIO())
    closed.close()
    with contextlib.redirect_stdout(closed):
        code = cli.main(["hygiene", str(tmp_path / "missing")])
    assert (code, "error: " in capsys.readouterr().err) == (2, True)


@pytest.mark.integration
def test_a_closed_stdout_fd_still_prints_the_pair_and_never_crashes(tmp_path):
    """#86 rule 3: with fd 1 closed the door still exits 2 with its cannot-judge line."""
    done = subprocess.run(
        ["bash", "-c", '"$PY" -m quality.cli hygiene "$MISSING" >&-'],
        env={**os.environ, "PY": sys.executable, "MISSING": str(tmp_path / "missing")},
        text=True, check=False, stderr=subprocess.PIPE,
    )
    assert (done.returncode, done.stderr.startswith("error: ")) == (2, True), done.stderr


def test_an_example_line_prints_whole_however_long_and_escaped():
    long_line = 'reason = "' + "x" * 400 + '"'
    stream = io.StringIO()
    output.write_pair("e", (Example("write it", (long_line, "k = \x1b[35m\u202e")),), file=stream)
    assert stream.getvalue() == f"error: e\nnext: write it:\n    {long_line}\n    k = \\x1b[35m\\u202e\n"


@pytest.mark.parametrize(("term", "no_color"), [("xterm", None), ("xterm", "1"), ("dumb", None)], ids=["terminal", "NO_COLOR", "dumb"])
def test_an_example_is_painted_by_its_label_only_at_a_terminal(monkeypatch, term, no_color):
    monkeypatch.setenv("TERM", term)
    monkeypatch.delenv("NO_COLOR", raising=False)
    if no_color:
        monkeypatch.setenv("NO_COLOR", no_color)
    styled = written(Terminal, lambda stream: output.write_pair("e", (COPY,), file=stream))
    assert OWN_SGR.sub("", styled) == "error: e\nnext: write it in a.toml like this:\n    [[table]]\n    key = \"value\"\n    other = 1\n"
    assert ("\x1b[" in styled) is (term == "xterm" and no_color is None)
    assert not any("\x1b[" in line for line in styled.splitlines() if line.startswith("    "))
