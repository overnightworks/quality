import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from quality import check, cli
from quality.output import Glyph
from quality.report import FindingEvent, headline, printable
from quality.rules import Area, Rule
from tests.support.check import (
    area_block,
    area_finding_rows,
    area_row,
    assert_block_prints,
    assert_every_line_starts_with_the_door,
    assert_finding_vocabulary,
    hygiene_run,
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
        return hygiene_run(self.repo, self.locate_ruff).areas

    @property
    def area(self) -> check.AreaVerdict:
        return next(verdict for verdict in self.areas if verdict.area is Area.CODE)


@pytest.fixture
def drive_code(build_repo, capsys):
    """`drive(files, locate_ruff=…)` runs `hygiene` over a repository tracking `files`; a `Path` is run as it stands."""

    def drive(files: dict[str, str] | Path, locate_ruff: Callable[[], str] = installed_ruff) -> CodeRun:
        repo = files if isinstance(files, Path) else build_repo(files)
        code = cli.main(["hygiene", str(repo)], locate_ruff=locate_ruff, clock=lambda: 0.0)
        captured = capsys.readouterr()
        return CodeRun(repo, locate_ruff, code, captured.out, captured.err)

    return drive


@pytest.mark.integration
def test_a_security_finding_stands_as_a_row_with_its_fix_under_the_code_row(drive_code):
    run = drive_code({"src/p/a.py": f"x = 1\n{EVAL}"})
    (finding,) = run.area.findings
    assert (run.code, run.err) == (1, "")
    assert (finding.location, finding.event, finding.reason) == ("src/p/a.py:2", FindingEvent.INSECURE, EVAL_REASON)
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
    run = drive_code({"pyproject.toml": "[project]\nname = 'x'\n", path: source})
    (finding,) = run.area.findings
    assert (run.code, run.err) == (1, "")
    assert (finding.location, finding.event, finding.reason) == (location, FindingEvent.PRINT_CALL, reason)
    assert_block_prints(area_block(run.out, Area.CODE), run.area.findings, ())


@pytest.mark.integration
@pytest.mark.parametrize(
    ("directive", "codes"),
    [(OWNER, ["T201"]), ("# ruff: noqa: T201, T203 - the command line owns its output", ["T201", "T203"])],
    ids=["one code", "two codes"],
)
def test_an_owner_that_clears_no_finding_stands_as_an_unused_row_on_its_line(drive_code, directive, codes):
    run = drive_code({"src/p/a.py": f"x = 1\n{directive}\n"})
    (finding,) = run.area.findings
    assert (run.code, run.err) == (1, "")
    assert (finding.location, finding.event) == ("src/p/a.py:2", FindingEvent.UNUSED)
    assert all(code in finding.reason for code in codes)
    assert_block_prints(area_block(run.out, Area.CODE), run.area.findings, ())


@pytest.mark.integration
def test_a_code_finding_ends_in_no_full_stop_and_names_no_rule_id(drive_code):
    run = drive_code({"src/p/a.py": f"{PRINT}\n{EVAL}", "src/p/b.py": f"x = 1\n{OWNER}\n"})
    findings = run.area.findings
    assert {finding.event for finding in findings} == {FindingEvent.INSECURE, FindingEvent.PRINT_CALL, FindingEvent.UNUSED}
    for finding in findings:
        assert_finding_vocabulary(finding)
        assert headline(finding) not in run.out
    lines = (run.out + run.err).splitlines()
    assert [line for line in lines if line.lstrip().startswith((Rule.PYTHON_SECURITY, Rule.PYTHON_PRINT))] == []
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
    run = drive_code(files)
    assert [(location, row.event) for location, row in area_finding_rows(run.out, Area.CODE)] == rows
    assert run.err == ""


@pytest.mark.integration
def test_an_owner_stays_in_the_prelude_and_its_file_keeps_its_security_row(drive_code):
    run = drive_code({"src/p/door.py": f"{OWNER}\n{PRINT}\n{EVAL}", "src/p/other.py": f"{PRINT}\n"})
    rows = [(location, row.event) for location, row in area_finding_rows(run.out, Area.CODE)]
    assert run.out.splitlines()[0] == hygiene_run(run.repo, run.locate_ruff).reports[0]
    assert rows == [("src/p/door.py:3", FindingEvent.INSECURE), ("src/p/other.py:1", FindingEvent.PRINT_CALL)]
    assert (run.code, run.err) == (1, "")


@pytest.mark.integration
@pytest.mark.parametrize(
    ("path", "message"),
    [("src/p/a\x1b[31m\u202e.py", None), ("src/p/a.py", "bad \x1b[31m \u202e message")],
    ids=["path", "message"],
)
def test_a_row_prints_a_path_and_a_ruff_message_escaped(drive_code, build_repo, elsewhere, path, message):
    if message is None:
        run = drive_code({path: f"{PRINT}\n"})
        original = path
    else:
        repo = build_repo({path: f"{PRINT}\n"})
        entry = {**ruff_entry(code="T201", filename=os.path.realpath(repo / path)), "message": message}
        (elsewhere / "report.json").write_text(json.dumps([entry]))
        binary = fake_ruff(elsewhere, f"cat {elsewhere / 'report.json'}")
        run = drive_code(repo, lambda: str(binary))
        original = message
    assert_block_prints(area_block(run.out, Area.CODE), run.area.findings, ())
    assert printable(original) in run.out
    assert original not in run.out
    assert_every_line_starts_with_the_door((run.out + run.err).splitlines())
    assert (run.code, run.err) == (1, "")


@pytest.mark.integration
def test_a_finding_beside_a_rule_that_refused_stands_under_the_refused_row_with_the_exact_pair(drive_code, build_repo, elsewhere):
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
