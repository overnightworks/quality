import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from quality import check, cli
from quality.output import Glyph
from quality.report import FindingEvent, NoteEvent
from quality.rules import Area, Rule
from tests.support.check import (
    area_block,
    area_finding_rows,
    area_row,
    assert_block_prints,
    assert_every_line_starts_with_the_door,
    assert_finding_vocabulary,
    check_result,
    check_streams,
    extent_counts,
    installed_ruff,
)
from tests.support.python_lint import EVAL, EVAL_REASON, OWNER, PRINT, PRINT_REASON, fake_ruff, ruff_entry


@dataclass(frozen=True)
class CodeRun:
    repo: Path
    locate_ruff: Callable[[], str]
    code: int
    out: str
    err: str

    @property
    def areas(self) -> tuple[check.AreaVerdict, ...]:
        return check_result(self.repo, self.locate_ruff).areas

    @property
    def area(self) -> check.AreaVerdict:
        return next(verdict for verdict in self.areas if verdict.area is Area.CODE)


@pytest.fixture
def drive_code(build_repo, capsys):
    """`drive(files, locate_ruff=…)` runs the check over a repository tracking `files`; a `Path` is run as it stands."""

    def drive(files: dict[str, str] | Path, locate_ruff: Callable[[], str] = installed_ruff) -> CodeRun:
        repo = files if isinstance(files, Path) else build_repo(files)
        return CodeRun(repo, locate_ruff, *check_streams(capsys, repo, locate_ruff))

    return drive


@pytest.mark.integration
def test_a_security_finding_stands_as_a_row_with_its_fix_under_the_code_row(drive_code):
    """SECURITY-8: a security finding stands as an `insecure` row at its line, ruff's code and message its fact."""
    run = drive_code({"src/p/a.py": f"x = 1\n{EVAL}"})
    (finding,) = run.area.findings
    assert (run.code, run.err) == (1, "")
    assert (finding.location, finding.event, finding.fact) == ("src/p/a.py:2", FindingEvent.INSECURE, EVAL_REASON)
    assert_block_prints(area_block(run.out, Area.CODE), run.area.findings, ())


@pytest.mark.integration
@pytest.mark.parametrize(
    ("path", "source", "location", "reason"),
    [
        ("src/p/a.py", f"x = 1\n{PRINT}\n", "src/p/a.py:2", PRINT_REASON),
        ("scripts/run_it.py", '"""Runs it."""\nfrom pprint import pprint\npprint(1)\n', "scripts/run_it.py:3", "T203 `pprint` found"),
        ("src/p/a.py", f"import sys\n{PRINT[:-1]}, file=sys.stderr)\n", "src/p/a.py:2", PRINT_REASON),
        ("tests/test_a.py", f"{PRINT}\n", "tests/test_a.py:1", PRINT_REASON),
    ],
    ids=["print in src", "pprint in scripts", "print to stderr", "print in a test file"],
)
def test_a_print_stands_as_a_row_with_its_fix_under_the_code_row(drive_code, path, source, location, reason):
    """PRINT-1, PRINT-3, PRINT-4, PRINT-5: T201 and T203 stand as `print call` rows with ruff's message, a print to
    stderr and one in a test file included."""
    run = drive_code({"pyproject.toml": "[project]\nname = 'x'\n", path: source})
    (finding,) = run.area.findings
    assert (run.code, run.err) == (1, "")
    assert (finding.location, finding.event, finding.fact) == (location, FindingEvent.PRINT_CALL, reason)
    assert_block_prints(area_block(run.out, Area.CODE), run.area.findings, ())


@pytest.mark.integration
@pytest.mark.parametrize(
    ("directive", "codes"),
    [(OWNER, ["T201"]), ("# ruff: noqa: T201, T203 - the command line owns its output", ["T201", "T203"])],
    ids=["one code", "two codes"],
)
def test_an_owner_that_clears_no_finding_stands_as_an_unused_row_on_its_line(drive_code, directive, codes):
    """PRINT-15, PRINT-16: an owner that names no code found stands as an `unused` row on its line naming its codes,
    in a file with no print."""
    run = drive_code({"src/p/a.py": f"x = 1\n{directive}\n"})
    (finding,) = run.area.findings
    assert (run.code, run.err) == (1, "")
    assert (finding.location, finding.event) == ("src/p/a.py:2", FindingEvent.UNUSED)
    assert all(code in finding.fact for code in codes)
    assert_block_prints(area_block(run.out, Area.CODE), run.area.findings, ())


@pytest.mark.integration
def test_a_code_finding_ends_in_no_full_stop_and_names_no_rule_id(drive_code):
    """ROW-7, ROW-14: no code finding ends in a full stop or names a rule id."""
    run = drive_code({"src/p/a.py": f"{PRINT}\n{EVAL}", "src/p/b.py": f"x = 1\n{OWNER}\n", "src/p/c.py": f"{OWNER}\n{PRINT}\n"})
    findings = run.area.findings
    assert {finding.event for finding in findings} == {FindingEvent.INSECURE, FindingEvent.PRINT_CALL, FindingEvent.UNUSED}
    for finding in findings:
        assert_finding_vocabulary(finding)
    printed = run.out + run.err
    assert (Rule.PYTHON_SECURITY in printed, Rule.PYTHON_PRINT in printed) == (False, False)
    assert run.err == ""


@pytest.mark.integration
@pytest.mark.parametrize(
    ("files", "fix_follows"),
    [
        ({"src/p/a.py": f"{PRINT}\n{PRINT}\n"}, [False, True]),
        ({"src/p/a.py": f"{PRINT}\nfrom pprint import pprint\npprint(1)\n"}, [True, True]),
        ({"src/p/a.py": f"{EVAL}{EVAL}"}, [True, True]),
        ({"src/p/a.py": 'eval("1"); eval("2")\n'}, [False, True]),
        ({"src/p/a.py": EVAL, "src/p/b.py": EVAL}, [False, True]),
        ({"src/p/a.py": f"{PRINT}\n", "src/p/b.py": f"{PRINT}\n"}, [False, True]),
    ],
    ids=[
        "print twice",
        "print and pprint",
        "eval on two lines",
        "eval twice on one line",
        "eval on line one of two files",
        "print on line one of two files",
    ],
)
def test_equal_fixes_in_the_code_area_print_once(drive_code, files, fix_follows):
    """ROW-3: equal fixes of consecutive findings in the code area print once."""
    run = drive_code(files)
    assert [row.fix_follows for _, row in area_finding_rows(run.out, Area.CODE)] == fix_follows
    assert run.err == ""


@pytest.mark.integration
@pytest.mark.parametrize(
    ("files", "rows"),
    [
        ({"src/p/a.py": f"{PRINT}\n{EVAL}"}, [("src/p/a.py:2", FindingEvent.INSECURE), ("src/p/a.py:1", FindingEvent.PRINT_CALL)]),
        (
            {"src/p/a.py": f"{PRINT}\n", "src/p/b.py": EVAL},
            [("src/p/b.py:1", FindingEvent.INSECURE), ("src/p/a.py:1", FindingEvent.PRINT_CALL)],
        ),
    ],
    ids=["one file", "two files"],
)
def test_the_security_rows_stand_before_the_print_rows(drive_code, files, rows):
    """O14: an area's findings stand in the rule runner's order, the security rows before the print rows."""
    run = drive_code(files)
    assert [(location, row.event) for location, row in area_finding_rows(run.out, Area.CODE)] == rows
    assert run.err == ""


@pytest.mark.integration
def test_an_honoured_owner_stands_after_the_findings_and_its_file_keeps_its_security_row(drive_code):
    """PRINT-9, PRINT-13, PRINT-14, ROW-15: an honoured owner stands as a note after the findings, clears no print of
    another file and no S finding of its own."""
    run = drive_code({"src/p/door.py": f"{OWNER}\n{PRINT}\n{EVAL}", "src/p/other.py": f"{PRINT}\n"})
    rows = [(location, row.event) for location, row in area_finding_rows(run.out, Area.CODE)]
    assert [(note.location, note.event) for note in run.area.notes] == [("src/p/door.py:1", NoteEvent.ALLOWED)]
    assert rows == [("src/p/door.py:3", FindingEvent.INSECURE), ("src/p/other.py:1", FindingEvent.PRINT_CALL)]
    assert_block_prints(area_block(run.out, Area.CODE), run.area.findings, run.area.notes)
    assert (run.out.splitlines()[0].split()[1], run.code, run.err) == (next(iter(Area)), 1, "")


@pytest.mark.integration
@pytest.mark.parametrize(
    ("files", "python_files"),
    [
        ({"README.md": "x\n"}, 0),
        ({"src/p/a.py": "x = 1\n", "src/p/b.py": "y = 2\n"}, 2),
        ({"pyproject.toml": "[project]\nname = 'x'\n", "src/p/a.py": "x = 1\n", "tests/test_a.py": "x = 1\n"}, 2),
    ],
    ids=["no python file", "two python files", "a test file counts"],
)
def test_a_clean_code_row_counts_its_rules_and_every_python_file_ruff_judged(drive_code, files, python_files):
    """PRINT-2, ROW-12: a clean code row counts its rules and every Python file ruff judged, for both rules."""
    run = drive_code(files)
    row = area_row(run.out, Area.CODE)
    rules = sum(rule.area is Area.CODE for rule in Rule)
    assert (row.glyph, row.event, extent_counts(row.fact), run.err) == (Glyph.CLEAN, cli.AreaEvent.CLEAN, [rules, python_files], "")
    assert [verdict.extent for verdict in run.area.verdicts] == [python_files] * 2


@pytest.mark.integration
@pytest.mark.parametrize(
    ("files", "python_files", "locations"),
    [
        ({"src/p/a.py": f"{PRINT}  # noqa: T201 - the door prints here\n"}, 1, []),
        ({"src/p/a.py": f"{OWNER}\n{PRINT}\n"}, 1, ["src/p/a.py:1"]),
        (
            {"src/p/a.py": f"{OWNER}\n# ruff: noqa: T203 - the door pretty-prints\n{PRINT}\nfrom pprint import pprint\npprint(1)\n"},
            1,
            ["src/p/a.py:1", "src/p/a.py:2"],
        ),
        ({"src/p/a.py": f"{OWNER}\n{PRINT}\n", "src/p/b.py": f"{OWNER}\n{PRINT}\n"}, 2, ["src/p/a.py:1", "src/p/b.py:1"]),
    ],
    ids=["line noqa", "one owner", "two directives in one file", "an owner in each of two files"],
)
def test_a_clean_code_row_counts_each_honoured_owner_as_allowed(drive_code, files, python_files, locations):
    """PRINT-14, O15: each honoured owner stands as an `allowed` note on its own line and a clean row counts it; a
    reasoned line noqa is no owner."""
    run = drive_code(files)
    row = area_row(run.out, Area.CODE)
    rules = sum(rule.area is Area.CODE for rule in Rule)
    assert [note.location for note in run.area.notes] == locations
    assert all(note.event is NoteEvent.ALLOWED for note in run.area.notes)
    assert extent_counts(row.fact) == [rules, python_files, *([len(locations)] if locations else [])]
    assert_block_prints(area_block(run.out, Area.CODE), (), run.area.notes)
    assert (row.glyph, run.code, run.err) == (Glyph.CLEAN, 0, "")


@pytest.mark.integration
@pytest.mark.parametrize(
    ("path", "reason", "original", "escaped"),
    [
        ("src/p/a\x1b[31m\u202e.py", "why", "src/p/a\x1b[31m\u202e.py", "src/p/a\\x1b[31m\\u202e.py"),
        ("src/p/a.py", "why \x1b[31m \u202e here", "why \x1b[31m \u202e here", "why \\x1b[31m \\u202e here"),
    ],
    ids=["path", "reason"],
)
def test_an_owner_note_prints_its_path_and_reason_escaped(drive_code, path, reason, original, escaped):
    """PRINT-14, MASK-4, MASK-6, MASK-7, MASK-11: an owner note's path and reason print escaped, and no printed line
    starts with repository text."""
    run = drive_code({path: f"# ruff: noqa: T201 - {reason}\n{PRINT}\n"})
    assert_block_prints(area_block(run.out, Area.CODE), (), run.area.notes)
    assert escaped in run.out
    assert original not in run.out
    assert_every_line_starts_with_the_door((run.out + run.err).splitlines())
    assert run.err == ""


@pytest.mark.integration
@pytest.mark.parametrize(
    ("code", "locations"),
    [("S307", ["src/p/door.py:1"]), ("T201", [])],
    ids=["python-security refused", "python-print refused"],
)
def test_an_owner_note_stands_under_a_refused_row_only_where_its_rule_judged(drive_code, build_repo, elsewhere, code, locations):
    """PRINT-14, SECURITY-26, SECURITY-28: an owner note stands under the refused `code` row where python-print judged
    beside a python-security that could not, and none stands where python-print itself could not judge."""
    repo = build_repo({"src/p/a.py": "x = 1\nx = '''open\n", "src/p/door.py": f"{OWNER}\n{PRINT}\n"})
    entries = [
        ruff_entry(code=code, filename=os.path.realpath(repo / "src" / "p" / "a.py")),
        {**ruff_entry(code="T201", filename=os.path.realpath(repo / "src" / "p" / "door.py")), "message": "`print` found"},
    ]
    (elsewhere / "report.json").write_text(json.dumps(entries))
    binary = fake_ruff(elsewhere, f"cat {elsewhere / 'report.json'}")
    run = drive_code(repo, lambda: str(binary))
    row = area_row(run.out, Area.CODE)
    assert (row.glyph, row.event, run.code) == (Glyph.COULD_NOT_CHECK, cli.AreaEvent.NOT_CHECKED, 2)
    assert [note.location for note in run.area.notes] == locations
    assert_block_prints(area_block(run.out, Area.CODE), run.area.findings, run.area.notes)
    assert run.err == f"error: {cli.refusal_error(run.areas)}\nnext: {cli.REFUSED_AREAS_NEXT.text}\n"


@pytest.mark.integration
@pytest.mark.parametrize(
    ("path", "message", "original", "escaped"),
    [
        ("src/p/a\x1b[31m\u202e.py", None, "src/p/a\x1b[31m\u202e.py", "src/p/a\\x1b[31m\\u202e.py"),
        ("src/p/a.py", "bad \x1b[31m \u202e message", "bad \x1b[31m \u202e message", "bad \\x1b[31m \\u202e message"),
    ],
    ids=["path", "message"],
)
def test_a_row_prints_a_path_and_a_ruff_message_escaped(drive_code, build_repo, elsewhere, path, message, original, escaped):
    """MASK-2, MASK-6, MASK-7, MASK-11: a finding's path and ruff's message print escaped, and no printed line starts
    with repository text."""
    if message is None:
        run = drive_code({path: f"{PRINT}\n"})
    else:
        repo = build_repo({path: f"{PRINT}\n"})
        entry = {**ruff_entry(code="T201", filename=os.path.realpath(repo / path)), "message": message}
        (elsewhere / "report.json").write_text(json.dumps([entry]))
        binary = fake_ruff(elsewhere, f"cat {elsewhere / 'report.json'}")
        run = drive_code(repo, lambda: str(binary))
    assert_block_prints(area_block(run.out, Area.CODE), run.area.findings, ())
    assert escaped in run.out
    assert original not in run.out
    assert_every_line_starts_with_the_door((run.out + run.err).splitlines())
    assert (run.code, run.err) == (1, "")


@pytest.mark.integration
def test_a_finding_beside_a_rule_that_refused_stands_under_the_refused_row_with_the_exact_pair(drive_code, build_repo, elsewhere):
    """REFUSAL-7, SECURITY-26, SECURITY-28: a python-security finding beside a python-print that cannot read a file's
    comments stands under the `✗` row, and the run exits 2."""
    repo = build_repo({"src/p/a.py": "print(argv)\nx = '''open\n", "src/p/b.py": "x = 1\n"})
    entries = [
        {**ruff_entry(code="T201", filename=os.path.realpath(repo / "src" / "p" / "a.py")), "message": "`print` found"},
        ruff_entry(code="S307", filename=os.path.realpath(repo / "src" / "p" / "b.py")),
    ]
    (elsewhere / "report.json").write_text(json.dumps(entries))
    binary = fake_ruff(elsewhere, f"cat {elsewhere / 'report.json'}")
    run = drive_code(repo, lambda: str(binary))
    row = area_row(run.out, Area.CODE)
    (finding,) = run.area.findings
    assert (finding.location, finding.event) == ("src/p/b.py:1", FindingEvent.INSECURE)
    assert ((row.glyph, row.event), run.code) == ((Glyph.COULD_NOT_CHECK, cli.AreaEvent.NOT_CHECKED), 2)
    assert_block_prints(area_block(run.out, Area.CODE), run.area.findings, ())
    assert run.err == f"error: {cli.refusal_error(run.areas)}\nnext: {cli.REFUSED_AREAS_NEXT.text}\n"


@pytest.mark.integration
def test_a_file_that_opens_with_a_byte_order_mark_is_judged(drive_code):
    run = drive_code({"src/p/a.py": f"\ufeff{PRINT}\n"})
    (finding,) = run.area.findings
    assert (finding.location, run.code, run.err) == ("src/p/a.py:1", 1, "")
    assert finding.location in run.out
