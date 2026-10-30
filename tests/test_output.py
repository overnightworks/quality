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


def test_the_pair_labels_and_escapes_each_text():
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


MODULE_DOOR = [sys.executable, "-m", "quality.cli"]
CONSOLE_SCRIPT_DOOR = [str(Path(sys.executable).parent / "quality")]


def quality_door(*arguments: str, cwd: Path, door: list[str] = MODULE_DOOR, **streams) -> subprocess.CompletedProcess[str]:
    return subprocess.run([*door, *arguments], cwd=cwd, text=True, check=False, **streams)


@pytest.fixture
def env_and_broken_compose(build_repo) -> Path:
    return build_repo({".env": "A=1\n", "compose.yaml": "services: [\n"})


@pytest.mark.parametrize("door", [MODULE_DOOR, CONSOLE_SCRIPT_DOOR], ids=["module", "console script"])
def test_a_merged_pipe_shows_the_hygiene_lines_in_run_order(env_and_broken_compose, door):
    """#86 rule 3, cells 10 and 12a: the notes, the area rows with their findings, then the pair."""
    done = quality_door(
        "hygiene", ".", cwd=env_and_broken_compose, door=door, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    prefixes = [
        "size: ", "clone: ", "import-contract: ", "workflow-security runs offline", "  ✗ files", "env-file .env - ", "  Fix: ", "root-layout .env - ", "  Fix: ",
        "  · debt", "  ✓ imports", "  ✓ secrets", "  ✓ workflows", "  ✓ dependencies", "  ✓ code", "error: ", "next: ",
    ]
    lines = done.stdout.splitlines()
    assert all(line.startswith(prefix) for line, prefix in zip(lines, prefixes, strict=True)), done.stdout


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
