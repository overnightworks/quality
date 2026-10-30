import io
import re
import unicodedata
from enum import StrEnum

import pytest

from quality import output
from quality.output import Glyph, Row, SubLine, Verdict

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
    assert text == "  ✓ fi\\x0ales    clean          a\\x1b[35mb\n"


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


def test_the_pair_goes_to_stderr_words_and_escapes_each_text():
    stream = io.StringIO()
    output.write_pair("a\x1b[35mb", "c\nd", file=stream)
    assert stream.getvalue() == "error: a\\x1b[35mb\nnext: c\\x0ad\n"


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
    pair = written(Terminal, lambda stream: output.write_pair("e", "n", file=stream))
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
    output.write_pair("e", "n", file=stream)
    assert "\x1b" not in stream.getvalue()
