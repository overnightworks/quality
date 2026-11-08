import io
import os
import re
import unicodedata
from enum import StrEnum

import pytest

from quality import cli, output
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
from tests.support.terminal import Terminal, by_role, without_sgr


class Event(StrEnum):
    CLEAN = "clean"
    FINDING = "finding"


def on(stream: io.StringIO, **environment: str) -> output.Channel:
    """The channel a door opens on `stream` under a terminal's environment, overridden by `environment`."""
    return output.open_channel(stream, {"TERM": "xterm"} | environment)


def written(stream_kind: type[io.StringIO], write, **environment: str) -> str:
    stream = stream_kind()
    write(on(stream, **environment))
    return stream.getvalue()


def plain(rows: tuple[Row, ...]) -> str:
    return written(io.StringIO, lambda channel: output.write_rows(rows, channel))


def verdict_line(**counts) -> str:
    defaults = {"findings": 0, "areas_with_findings": 0, "clean_areas": 7, "areas": 7, "notes": (), "seconds": 0}
    return written(io.StringIO, lambda channel: output.write_verdict(Verdict(**{**defaults, **counts}), channel))


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (Row(Glyph.CLEAN, "dependencies", Event.CLEAN, "1 lockfile"), "  ✓ dependencies  clean          1 lockfile\n"),
        (Row(Glyph.CLEAN, "files", Event.CLEAN), "  ✓ files         clean\n"),
        (Row(Glyph.NEEDS_YOU, "files", Event.FINDING, count=1), "  ! files         1 finding\n"),
        (Row(Glyph.NEEDS_YOU, "code", Event.FINDING, "see below", count=3), "  ! code          3 findings     see below\n"),
        (Row(Glyph.NOTE, "debt", Event.CLEAN, "measured only"), "  · debt          clean          measured only\n"),
        (
            Row(Glyph.WILL_CHANGE, "debt", cli.AreaEvent.TO_LOWER, "quality/baseline.toml", count=3, counts_plural=False),
            "  ~ debt          3 to lower     quality/baseline.toml\n",
        ),
    ],
    ids=["fact", "empty fact is stripped", "one", "several", "note", "a count that takes no plural"],
)
def test_a_row_prints_its_glyph_subject_event_and_fact_in_columns(row, expected):
    """ROW-1, ROW-9, ROW-10: a row prints its glyph, subject, event and fact in columns, with or without a count."""
    assert plain((row,)) == expected


def test_sub_lines_stand_under_their_row_with_their_own_glyph():
    row = Row(Glyph.COULD_NOT_CHECK, "files", Event.CLEAN, "2 reasons", sub_lines=(SubLine(Glyph.COULD_NOT_CHECK, "first"), SubLine(Glyph.COULD_NOT_CHECK, "second")))
    assert plain((row,)).splitlines()[1:] == ["    ✗ first", "    ✗ second"]


def test_the_lines_below_a_sub_line_stand_deeper_and_escaped():
    row = Row(Glyph.NEEDS_YOU, "loosenings", Event.CLEAN, sub_lines=(SubLine(Glyph.NEEDS_YOU, "what", whole=True, below=("reason: a\x1b[8mb", "second")),))
    assert plain((row,)).splitlines() == ["  ! loosenings    clean", "    ! what", "        reason: a\\x1b[8mb", "        second"]


@pytest.mark.parametrize("whole", [False, True], ids=["fitted", "whole"])
def test_a_line_below_a_sub_line_is_painted_as_a_rows_fact_is(whole):
    fact_row = Row(Glyph.NEEDS_YOU, "loosenings", Event.CLEAN, "reason")
    below_row = Row(Glyph.NEEDS_YOU, "loosenings", Event.CLEAN, sub_lines=(SubLine(Glyph.NEEDS_YOU, "what", whole=whole, below=("reason",)),))
    fact_channel = written(Terminal, lambda channel: output.write_rows((fact_row,), channel))
    below_channel = written(Terminal, lambda channel: output.write_rows((below_row,), channel))
    glyph, start, end = re.fullmatch(r"  (\x1b\[[0-9;]*m!\x1b\[[0-9;]*m) loosenings    clean          (\x1b\[[0-9;]*m)reason(\x1b\[[0-9;]*m)\n", fact_channel).groups()
    assert below_channel == f"  {glyph} loosenings    clean\n    {glyph} what\n{start}        reason{end}\n"


def test_a_row_with_a_fix_ends_in_its_fix_line():
    """ROW-1: a row with a fix ends in its `fix:` line."""
    row = Row(Glyph.NOTE, "debt", Event.CLEAN, "not recorded", fix="run it like this")
    assert plain((row,)).splitlines() == ["  · debt          clean          not recorded", "      fix: run it like this"]


def test_repository_text_in_a_row_is_escaped_before_it_is_padded():
    """MASK-1, MASK-6, MASK-9."""
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
        ({"ceiling": "ceiling lowered by 2 entries", "seconds": 2}, "✓ clean · ceiling lowered by 2 entries · 7 areas · 2s\n"),
        (
            {"findings": 1, "areas_with_findings": 1, "clean_areas": 6, "ceiling": "ceiling lowered by 1 entry"},
            "! 1 finding in 1 area · 6 areas clean · ceiling lowered by 1 entry · <1s\n",
        ),
        (
            {"would_change": True, "clean_areas": 0, "areas": 1, "ceiling": "1 ceiling entry would fall"},
            "~ dry run · 1 ceiling entry would fall · nothing written · <1s\n",
        ),
        (
            {"findings": 1, "areas_with_findings": 1, "clean_areas": 5, "would_change": True, "ceiling": "1 ceiling entry would fall"},
            "! 1 finding in 1 area · 5 areas clean · 1 ceiling entry would fall · nothing written · <1s\n",
        ),
    ],
    ids=["clean", "one finding", "several", "no clean area is left out", "measured only", "partly measured with findings", "a lowered ceiling", "a lowered ceiling beside a finding", "a dry run", "a dry run beside a finding"],
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
    assert written(io.StringIO, lambda channel: output.write_pair("e", steps, channel)) == f"error: e\n{expected}"


def test_the_pair_labels_and_escapes_each_text():
    pair = written(io.StringIO, lambda channel: output.write_pair("a\x1b[35mb", (Command("c\nd"), Instruction("e\x1b")), channel))
    assert pair == "error: a\\x1b[35mb\nnext:\n    c\\x0ad\n    e\\x1b\n"


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


def test_styled_rows_hold_exactly_the_plain_words():
    rows = (
        Row(Glyph.CLEAN, "files", Event.CLEAN, "ok"),
        Row(Glyph.NEEDS_YOU, "code", Event.FINDING, count=2, sub_lines=(SubLine(Glyph.COULD_NOT_CHECK, "why"),)),
        Row(Glyph.NOTE, "debt", Event.CLEAN, "measured only"),
    )
    styled = written(Terminal, lambda channel: output.write_rows(rows, channel))
    assert "\x1b[" in styled
    assert without_sgr(styled) == plain(rows)


def test_a_styled_verdict_and_pair_hold_exactly_the_plain_words():
    verdict = Verdict(1, 1, 6, 7, ("debt measured, not enforced",), 3)
    assert without_sgr(written(Terminal, lambda channel: output.write_verdict(verdict, channel))) == verdict_line(
        findings=1, areas_with_findings=1, clean_areas=6, notes=("debt measured, not enforced",), seconds=3
    )
    pair = written(Terminal, lambda channel: output.write_pair("e", (Command("n"),), channel))
    assert ("\x1b[" in pair, without_sgr(pair)) == (True, "error: e\nnext: n\n")


def test_repository_text_reaches_a_terminal_only_as_escaped_text():
    """MASK-1, MASK-6, MASK-7."""
    row = Row(Glyph.COULD_NOT_CHECK, "files", Event.CLEAN, "\x1b[35m\u202e")
    styled = written(Terminal, lambda channel: output.write_rows((row,), channel))
    remaining = without_sgr(styled)
    assert "\\x1b[35m\\u202e" in remaining
    assert [character for character in remaining if unicodedata.category(character) in ("Cc", "Cf", "Zl", "Zp") and character != "\n"] == []


@pytest.mark.parametrize(("stream_kind", "environment"), [(Terminal, {"NO_COLOR": "1"}), (io.StringIO, {})], ids=["no color", "pipe"])
def test_plain_output_holds_no_escape_byte(stream_kind, environment):
    stream = stream_kind()
    channel = on(stream, **environment)
    output.write_rows((Row(Glyph.CLEAN, "files", Event.CLEAN),), channel)
    output.write_pair("e", (Command("n"),), channel)
    assert "\x1b" not in stream.getvalue()


def finding(path: str, event: FindingEvent, fact: str = "a fact", fix: str = "a fix", line: int | None = None) -> Finding:
    return Finding("some-rule", path, line, fact, fix, event=event)


def note(path: str, fact: str = "a note") -> Note:
    return Note(path, NoteEvent.ALLOWED, fact)


def findings_written(findings: list[Finding], stream_kind: type[io.StringIO] = io.StringIO, notes: list[Note] | None = None, **environment: str) -> str:
    return written(stream_kind, lambda channel: output.write_findings_and_notes(findings, notes or [], channel), **environment)


def test_a_moved_note_row_is_a_clean_row():
    printed = findings_written([], notes=[Note("src/new.py", NoteEvent.MOVED, "a fact")])
    assert printed.lstrip().startswith(Glyph.CLEAN)


@pytest.mark.parametrize(
    ("planned", "printed"),
    [
        pytest.param(NoteEvent.LOWER, "    ~ src/a.py  lower  a fact\n", id="lower"),
        pytest.param(NoteEvent.DROP, "    ~ src/a.py  drop  a fact\n", id="drop"),
        pytest.param(NoteEvent.MOVE, "    ~ src/a.py  move  a fact\n", id="move"),
        pytest.param(NoteEvent.RECORD, "    ~ src/a.py  record  a fact\n", id="record"),
    ],
)
def test_each_word_a_dry_run_says_is_a_will_change_row(planned, printed):
    assert findings_written([], notes=[Note("src/a.py", planned, "a fact")]) == printed


def test_a_will_change_row_at_a_terminal_paints_its_glyph_and_its_fact():
    printed = findings_written([], Terminal, notes=[Note("src/a.py", NoteEvent.LOWER, "a fact")])
    assert by_role(printed, Glyph.WILL_CHANGE) == "    {glyph}~{reset} src/a.py  lower  {other}a fact{reset}\n"


def test_a_dry_runs_glyph_has_a_colour_of_its_own():
    others = {style for glyph, style in output.GLYPH_STYLE.items() if glyph is not Glyph.WILL_CHANGE}
    foreground_colours = range(30, 38)
    assert output.GLYPH_STYLE[Glyph.WILL_CHANGE] not in others
    assert int(output.GLYPH_STYLE[Glyph.WILL_CHANGE]) in foreground_colours


def test_a_dry_runs_verdict_is_painted_in_its_glyphs_colour():
    assert output.VERDICT_STYLE[Glyph.WILL_CHANGE].endswith(output.GLYPH_STYLE[Glyph.WILL_CHANGE])


def area_row_indent() -> int:
    row = plain((Row(Glyph.NEEDS_YOU, "files", Event.FINDING, count=1),))
    return len(row) - len(row.lstrip(" "))


def test_a_finding_prints_its_row_then_its_fix_deeper_than_an_area_row():
    """ROW-2: a finding stands under its area row, deeper than it, and its fix stands deeper still."""
    (row,) = finding_rows(findings_written([finding("a.env", FindingEvent.TRACKED, "the fact", "the fix")]), "a.env")
    assert (row.event, row.fact, row.fix, row.fix_follows) == (FindingEvent.TRACKED, "the fact", "the fix", True)
    assert area_row_indent() < row.indent < row.fix_indent


def test_the_event_and_the_fact_start_at_one_column_after_the_longest_location_and_event():
    """ROW-4: the findings of an area start their event and their fact at one column each, after the longest entry."""
    findings = [
        finding("a.env", FindingEvent.TRACKED, "first fact", "one"),
        finding("deploy/longer.env", FindingEvent.PUBLISHED_PORT, "second fact", "two"),
        finding("mid.env", FindingEvent.UNUSED, "third fact", "three"),
    ]
    rows = [line for line in findings_written(findings).splitlines() if line.lstrip().startswith("!")]
    event_columns = {line.index(finding.event.value) for line, finding in zip(rows, findings, strict=True)}
    fact_columns = {line.index(finding.fact) for line, finding in zip(rows, findings, strict=True)}
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
    """ROW-3: one fix follows the last finding of each run of equal fixes."""
    findings = [finding(f"{number}.env", FindingEvent.TRACKED, fix=f"fix {fix}") for number, fix in enumerate(fixes)]
    printed = findings_written(findings)
    rows = [row for number in range(len(fixes)) for row in finding_rows(printed, f"{number}.env")]
    assert [row.fix_follows for row in rows] == fix_follows
    assert [row.fix for row in rows] == [f"fix {fix}" for fix in fixes]
    assert printed.count(f"{output.FIX_LABEL} ") == len(fix_follows) - fix_follows.count(False)


def test_an_event_findings_detail_prints_escaped_under_its_row_before_the_fix():
    """MASK-2, ROW-5: a finding's detail lines stand escaped behind the marker under its row, before its fix."""
    detail = ("one\n::error::d", "", "  two\x1b")
    found = Finding("some-rule", "a.env", None, "a fact", "a fix", detail=detail, event=FindingEvent.BROKEN)
    printed = findings_written([found])
    block = decode_block(printed.splitlines())
    (row,) = [line for line in block if isinstance(line, FindingLine)]
    assert row.row.detail == tuple(printable(line) for line in detail)
    assert (row.row.fix, row.row.fix_follows) == ("a fix", True)
    assert f"{output.FIX_INDENT}{output.DETAIL_MARKER}" in printed.splitlines()
    assert_block_order(block)
    assert without_sgr(findings_written([found], Terminal)) == printed


def test_at_a_terminal_each_detail_line_is_painted_as_an_area_rows_fact_is(painted_like_an_area_row):
    _, _, fact_start, fact_end = painted_like_an_area_row
    found = Finding("some-rule", "a.env", None, "a fact", "a fix", detail=("one", "two"), event=FindingEvent.BROKEN)
    styled = findings_written([found], Terminal).splitlines()
    detail_lines = [line for line in styled if output.DETAIL_MARKER in line]
    assert (len(detail_lines), all(line.startswith(fact_start) and line.endswith(fact_end) for line in detail_lines)) == (2, True)


ENTRY = Example("copy this", ("[[table]]", 'key = "esc\x1b"'))


def test_a_fix_with_lines_to_copy_prints_its_label_then_each_line_deeper_and_escaped():
    """MASK-2, ROW-6: a fix with lines to copy ends its label with a colon and stands each line deeper and escaped."""
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
    """ROW-3, ROW-6: two findings that share lines to copy print the fix and the lines once."""
    printed = findings_written([finding(f"{number}.env", FindingEvent.SECRET, fix=ENTRY) for number in range(2)])
    block = decode_block(printed.splitlines())
    assert (sum(isinstance(line, FixLine) for line in block), sum(isinstance(line, CopyLine) for line in block)) == (1, len(ENTRY.lines))


def test_at_a_terminal_the_fix_and_its_lines_to_copy_stay_plain_and_the_words_equal_the_plain_ones():
    findings = [finding("a.env", FindingEvent.SECRET, fix=ENTRY)]
    styled = findings_written(findings, Terminal)

    def fix_and_copy_lines(printed: str) -> list[str]:
        return [line.text for line in decode_block(printed.splitlines()) if isinstance(line, FixLine | CopyLine)]

    assert fix_and_copy_lines(styled) == fix_and_copy_lines(findings_written(findings))
    assert without_sgr(styled) == findings_written(findings)


def test_a_finding_with_a_line_shows_its_location():
    """ROW-2: a finding with a line shows `path:line` as its location, one without shows the path."""
    findings = [finding("a.env", FindingEvent.TRACKED, line=7), finding("b.env", FindingEvent.TRACKED)]
    assert [finding_rows(findings_written(findings), candidate.location) != [] for candidate in findings] == [True, True]
    assert findings[0].location != findings[0].path


def test_repository_text_is_escaped_before_it_is_padded():
    """MASK-2, MASK-6, MASK-7, MASK-9."""
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
def painted_like_an_area_row() -> tuple[str, str, str, str]:
    """The paint an area row puts around its `!` and its fact, read from `write_rows` on a terminal."""
    styled = written(Terminal, lambda channel: output.write_rows((Row(Glyph.NEEDS_YOU, "files", Event.FINDING, "FACT"),), channel))
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
    assert without_sgr(styled) == findings_written(findings)


@pytest.mark.parametrize(("term", "no_color"), [("xterm", "1"), ("dumb", None)], ids=["NO_COLOR", "dumb"])
def test_a_terminal_that_asks_for_no_style_prints_no_escape_byte(term, no_color):
    environment = {"TERM": term} | ({"NO_COLOR": no_color} if no_color else {})
    assert "\x1b" not in findings_written([finding("a.env", FindingEvent.TRACKED)], Terminal, **environment)


def test_no_findings_and_no_notes_print_nothing():
    """ROW-9: an area with no finding and no note has nothing under its row."""
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


@pytest.mark.parametrize("on_a_note", [False, True], ids=["finding", "note"])
def test_an_event_prints_escaped_and_its_column_holds(on_a_note):
    """MASK-9."""
    class Hostile(StrEnum):
        ESCAPES = "\x1b\x1b"

    assert len(printable(Hostile.ESCAPES)) > len(FindingEvent.TRACKED)
    companion = finding("b.env", FindingEvent.TRACKED, "second fact")
    if on_a_note:
        findings, notes = [companion], [Note("a.toml", Hostile.ESCAPES, "first fact")]
    else:
        findings, notes = [finding("a.env", Hostile.ESCAPES, "first fact"), companion], []
    printed = findings_written(findings, notes=notes)
    first, second = (next(line for line in printed.splitlines() if marker in line) for marker in ("first fact", "second fact"))
    assert printable(Hostile.ESCAPES) in printed
    assert "\x1b" not in printed
    assert first.index("first fact") == second.index("second fact")


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


def test_a_note_row_is_painted_dim_as_a_whole_as_an_area_note_row_is():
    area = written(Terminal, lambda channel: output.write_rows((Row(Glyph.NOTE, "debt", Event.CLEAN, "FACT"),), channel))
    opening, closing = re.fullmatch(r"(\x1b\[[\d;]*m).*(\x1b\[[\d;]*m)\n", area).groups()
    plain_line = findings_written([], notes=[note("b.toml", "FACT")]).rstrip("\n")
    assert findings_written([], Terminal, notes=[note("b.toml", "FACT")]) == f"{opening}{plain_line}{closing}\n"


def test_a_notes_location_and_fact_are_escaped_before_they_are_padded():
    """MASK-4, MASK-6, MASK-7, MASK-9."""
    hostile = "x\nesc\x1b[31m\u202eend\u2028"
    printed = findings_written([], notes=[note(hostile, hostile), note("short")])
    (row,) = note_rows(printed, printable(hostile))
    assert row.fact == printable(hostile)
    assert [character for character in printed if unicodedata.category(character) in ("Cc", "Cf", "Zl", "Zp") and character != "\n"] == []
    short_row = next(line for line in printed.splitlines() if " short " in line)
    assert short_row.index(NoteEvent.ALLOWED.value) == next(line for line in printed.splitlines() if "x\\x0a" in line).index(NoteEvent.ALLOWED.value)


def test_counted_gives_the_noun_for_one_and_its_plural_for_any_other_count():
    """ROW-10: a count reads in the singular for one and in the plural for any other count."""
    assert [output.counted(count, "entry", "entries") for count in (1, 2)] == ["1 entry", "2 entries"]


def test_an_example_line_prints_whole_however_long_and_escaped():
    long_line = 'reason = "' + "x" * 400 + '"'
    pair = written(io.StringIO, lambda channel: output.write_pair("e", (Example("write it", (long_line, "k = \x1b[35m\u202e")),), channel))
    assert pair == f"error: e\nnext: write it:\n    {long_line}\n    k = \\x1b[35m\\u202e\n"


@pytest.mark.parametrize(("term", "no_color"), [("xterm", None), ("xterm", "1"), ("dumb", None)], ids=["terminal", "NO_COLOR", "dumb"])
def test_an_example_is_painted_by_its_label_only_at_a_terminal(term, no_color):
    environment = {"TERM": term} | ({"NO_COLOR": no_color} if no_color else {})
    styled = written(Terminal, lambda channel: output.write_pair("e", (COPY,), channel), **environment)
    assert without_sgr(styled) == "error: e\nnext: write it in a.toml like this:\n    [[table]]\n    key = \"value\"\n    other = 1\n"
    assert ("\x1b[" in styled) is (term == "xterm" and no_color is None)
    assert not any("\x1b[" in line for line in styled.splitlines() if line.startswith("    "))


def test_rows_share_the_subject_column_they_are_given():
    rows = (Row(Glyph.CLEAN, "o/a", Event.CLEAN, "first"), Row(Glyph.CLEAN, "o/a-longer-name", Event.FINDING, "second"))
    shown = written(io.StringIO, lambda channel: output.write_rows(rows, channel, subject_width=20))
    assert shown == (
        "  ✓ o/a                   clean          first\n"
        "  ✓ o/a-longer-name       finding        second\n"
    )


@pytest.mark.parametrize(
    ("subjects", "width"),
    [
        pytest.param(("a", "b"), 12, id="narrow subjects keep the default"),
        pytest.param(("a", "a\nb" * 4), 24, id="an escaped subject counts as printed"),
        pytest.param(("a", "表" * 7), 14, id="a wide character counts two columns"),
    ],
)
def test_the_subject_width_is_the_widest_escaped_subject(subjects, width):
    rows = tuple(Row(Glyph.CLEAN, subject, Event.CLEAN) for subject in subjects)
    assert output.subject_width(rows) == width


def test_next_alone_stands_on_its_channel():
    assert written(io.StringIO, lambda channel: output.write_next((Command("quality merge o/r#7"),), channel)) == "next: quality merge o/r#7\n"


def test_lines_of_an_artifact_stand_escaped_and_unpainted():
    shown = written(Terminal, lambda channel: output.write_lines(("| a\x1b[31mb |", ""), channel))
    assert shown == "| a\\x1b[31mb |\n\n"


def test_rendered_lines_are_what_a_pipe_shows():
    def write(channels):
        output.write_lines(("a\x1bb",), channels.out)
        output.write_pair("e", (Instruction("s"),), channels.err)

    assert output.rendered(write) == ("a\\x1bb", "error: e", "next: s")


@pytest.mark.parametrize(
    ("lines", "fence"),
    [(("a", "b"), "```"), (("a```b",), "````"), (("`", "a`````b", "``"), "``````")],
    ids=["no run", "three", "five"],
)
def test_a_code_block_fence_outruns_every_backtick_run(lines, fence):
    assert output.code_block(lines) == (fence, *lines, fence)


def test_the_step_summary_is_the_one_the_given_environment_names(tmp_path, monkeypatch):
    named, elsewhere = tmp_path / "named.md", tmp_path / "elsewhere.md"
    monkeypatch.setenv(output.STEP_SUMMARY_VARIABLE, str(elsewhere))
    output.append_step_summary(("| a |",), {output.STEP_SUMMARY_VARIABLE: str(named)})
    output.append_step_summary(("| b |",), {})
    assert (named.read_text(), elsewhere.exists()) == ("| a |\n", False)


@pytest.mark.parametrize(
    ("typed", "returned", "shown"),
    [
        ("yes\n", "yes", "Type \\x1b: "),
        ("\n", "", "Type \\x1b: "),
        ("", None, "Type \\x1b: \n"),
        ("yes", None, "Type \\x1b: \n"),
    ],
    ids=["an ended line", "Enter alone", "end of input", "an unended line"],
)
def test_ask_returns_the_line_a_person_ended_and_none_where_the_input_ended_first(typed, returned, shown):
    stream = io.StringIO()
    answer = output.ask("Type \x1b: ", output.Channel(stream, False, None), lambda: typed)
    written = stream.getvalue()
    assert (answer, written) == (returned, shown)


class _FlushRecordingStream(io.StringIO):
    """Holds what was written until flushed, as a terminal's stderr holds a prompt without a line end."""

    def __init__(self):
        super().__init__()
        self.flushed = ""

    def flush(self):
        self.flushed = self.getvalue()


def test_ask_shows_the_prompt_before_it_reads_the_line():
    stream = _FlushRecordingStream()
    shown_at_the_read = []

    def read_line():
        shown_at_the_read.append(stream.flushed)
        return "yes\n"

    output.ask("Type yes: ", output.Channel(stream, False, None), read_line)

    assert shown_at_the_read == ["Type yes: "]


def a_stream_that_takes_the_rows(tmp_path):
    return open(tmp_path / "rows", "w", buffering=1), True


def a_full_device(tmp_path):
    return open("/dev/full", "w", buffering=1), False


def a_pipe_whose_reader_left(tmp_path):
    reader, writer = os.pipe()
    os.close(reader)
    return os.fdopen(writer, "w", buffering=1), True


@pytest.mark.parametrize(
    "opened", [a_stream_that_takes_the_rows, a_full_device, a_pipe_whose_reader_left], ids=["a stream that takes them", "a full device", "a reader that left"],
)
def test_the_door_says_whether_its_stream_took_its_rows(opened, tmp_path):
    stream, took = opened(tmp_path)
    rows = (Row(Glyph.CLEAN, "files", Event.CLEAN),)
    with stream:
        door_stream = output.DoorStream(output.Channel(stream, False, None))
        assert door_stream.show(rows) is took
        assert door_stream.show(rows) is True


def test_a_closed_stream_takes_the_rows_of_the_door():
    assert output.DoorStream(output.Channel(None, False, None)).show((Row(Glyph.CLEAN, "files", Event.CLEAN),)) is True


def test_a_command_on_its_own_line_stands_below_a_bare_next():
    pair = written(io.StringIO, lambda channel: output.write_pair("e", (Command("c", own_line=True),), channel))
    assert pair == "error: e\nnext:\n    c\n"
    two = written(io.StringIO, lambda channel: output.write_pair("e", (Command("a"), Command("b", own_line=True)), channel))
    assert two == "error: e\nnext:\n    a\n    b\n"


def test_the_closing_line_is_its_glyph_and_parts_joined():
    line = written(io.StringIO, lambda channel: output.write_closing_line(Glyph.CLEAN, ("merged #7", "quality_gate on again", "3s"), channel))
    assert line == "✓ merged #7 · quality_gate on again · 3s\n"


@pytest.mark.parametrize(
    ("parts", "expected"),
    [(("feat/\u202ea",), "✓ feat/\\u202ea\n"), (("report only", "0 open on feat/\u202ea"), "✓ report only · 0 open on feat/\\u202ea\n")],
    ids=["the first part", "a further part"],
)
def test_a_closing_line_prints_each_part_escaped(parts, expected):
    assert written(io.StringIO, lambda channel: output.write_closing_line(Glyph.CLEAN, parts, channel)) == expected


def test_the_step_output_is_the_one_the_given_environment_names(tmp_path, monkeypatch):
    named, elsewhere = tmp_path / "named", tmp_path / "elsewhere"
    output.append_step_output("sonar", "off", {"GITHUB_OUTPUT": str(named)})
    monkeypatch.setenv("GITHUB_OUTPUT", str(elsewhere))
    output.append_step_output("sonar", "off", {})
    assert (named.read_text(), elsewhere.exists()) == ("sonar=off\n", False)


@pytest.mark.parametrize("glyph", list(output.VERDICT_STYLE), ids=[str(glyph) for glyph in output.VERDICT_STYLE])
def test_a_closing_line_paints_its_first_part_in_its_glyphs_verdict_style(glyph):
    styled = written(Terminal, lambda channel: output.write_closing_line(glyph, ("first", "then"), channel))
    reset = output.RESET.removeprefix("\x1b[").removesuffix("m")
    assert re.findall(r"\x1b\[([0-9;]*)m", styled) == [output.VERDICT_STYLE[glyph], reset, output.DIM, reset]
    assert without_sgr(styled) == f"{glyph} first · then\n"
