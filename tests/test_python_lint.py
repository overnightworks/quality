import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from quality import check, cli, installation, python_lint, scanners
from quality.output import Glyph
from quality.python_lint import LintError, RuffFinding, UnparsablePython
from quality.report import FindingEvent, headline, printable
from quality.rules import Area, Rule
from tests.support.check import (
    area_block,
    area_finding_rows,
    area_row,
    assert_block_prints,
    assert_every_line_starts_with_the_door,
    assert_finding_vocabulary,
    env_row_stands,
    hygiene_run,
    installed_ruff,
    is_clean,
    loosen_requirement,
    refusal_row,
    refusals,
)
from tests.support.check import hygiene_output as run
from tests.support.layout import in_index
from tests.support.scanners import never_located, nothing_installed

EVAL = 'eval("1")\n'
EVAL_REASON = "S307 Use of possibly insecure function; consider using `ast.literal_eval`"
GREEN_NOQA_LINES = [
    "run(argv)  # noqa: S603 - argv is fixed words",
    "run(argv)  # noqa: S603, S607 - argv is fixed words",
    "run(argv)  # noqa: S603 - argv is fixed words  # a closing remark",
]
CODE_IN_SECOND_DIRECTIVE = "run(argv)  # noqa: S607 - argv is fixed words  # noqa: S603 - argv is fixed words"
PRINT = 'print("x")'
PRINT_REASON = "T201 `print` found"
OWNER = "# ruff: noqa: T201 - the command line owns its output"


def record(code: str = "S603", line: int = 1, noqa_line: int = 1, path: str = "src/p/a.py") -> RuffFinding:
    return RuffFinding(path, code, "`subprocess` call: check for execution of untrusted input", line, noqa_line)


def print_record(text: str, path: str = "src/p/a.py") -> RuffFinding:
    """The T201 finding ruff reports on the first line of `text` that calls print."""
    line = next(number for number, content in enumerate(text.splitlines(), 1) if content.startswith("print("))
    return RuffFinding(path, "T201", "`print` found", line, line)


def linted(records: list[RuffFinding], texts: dict[str, str], test_files: frozenset[str] = frozenset()) -> python_lint.Lint:
    return python_lint.Lint(tuple(records), texts, test_files)


def judged(text: str, finding: RuffFinding, test_files: frozenset[str] = frozenset()) -> list[str]:
    findings = python_lint.judge_security(linted([finding], {finding.path: text}, test_files))
    return [f"{item.location} {item.reason}" for item in findings]


def judged_prints(text: str, records: list[RuffFinding]) -> tuple[list[str], tuple[str, ...]]:
    """The python-print findings as `<location> <reason>`, and the report lines."""
    result = python_lint.judge_print(linted(records, {"src/p/a.py": text}))
    return [f"{item.location} {item.reason}" for item in result.findings], result.reports


def rule_verdict(repo: Path, rule: Rule = Rule.PYTHON_SECURITY) -> check.RuleVerdict:
    return next(verdict for verdict in check.run_hygiene(repo, installed_ruff, never_located).verdicts if verdict.rule == rule)


def flagged_paths(repo: Path, rule: Rule = Rule.PYTHON_SECURITY) -> list[str]:
    verdict = rule_verdict(repo, rule)
    assert verdict.refusal is None, verdict.refusal
    return [finding.path for finding in verdict.findings]


def fake_ruff(directory: Path, on_check: str, version: str | None = None) -> Path:
    """A ruff that reports `version` and runs `on_check` (shell) for any other call."""
    binary = directory / "ruff"
    reported = version or installation.required_version("ruff")
    binary.write_text(f'#!/bin/sh\nif [ "$1" = "--version" ]; then echo "ruff {reported}"; exit 0; fi\n{on_check}\n')
    binary.chmod(0o755)
    return binary


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


@pytest.mark.parametrize("line", GREEN_NOQA_LINES)
def test_a_reasoned_noqa_naming_the_code_at_noqa_row_is_green(line):
    """Sentence 5, cell 10: the canonical form names the code, and its reason holds a letter and no comma."""
    assert judged(f"{line}\n", record()) == []


@pytest.mark.parametrize(
    "line",
    [
        "run(argv)",
        "run(argv)  # noqa: S603",
        "run(argv)  # noqa",
        "run(argv)  # type: ignore[x]  # noqa: S603",
        "run(argv)  # noqa: S603  # pragma: no cover",
        "run(argv)  # noqa: S603: argv is fixed words",
        "run(argv)  # noqa: S603 - fixed argv, never a shell",
        "run(argv)  # noqa: S607 - argv is fixed words",
        "run(argv)  # noqa: S603 - 42",
        "run(argv)  # noqa:S603 - argv is fixed words",
        "run(argv)  # noqa: S603 - argv is fixed words  # noqa: E501",
        CODE_IN_SECOND_DIRECTIVE,
        "run(argv)  # noqa: S603 - argv is fixed words  # noqa: S607 - argv is fixed words",
        'run("# noqa: S603 - argv is fixed words")',
    ],
    ids=["none", "no reason", "blanket", "type ignore as reason", "pragma as reason", "colon form", "comma in reason",
         "wrong code", "no letter in reason", "no space after colon", "second bare directive",
         "code in second reasoned directive", "second reasoned directive", "inside a string"],
)
def test_every_other_noqa_form_is_red(line):
    """Sentence 5, cells 11 and 12: only the canonical form is green; ruff reads only a line's first directive, so a
    second one, bare or reasoned, is not hidden."""
    assert judged(f"{line}\n", record()) == ["src/p/a.py:1 S603 `subprocess` call: check for execution of untrusted input"]


@pytest.mark.parametrize(
    ("text", "line"),
    [("run(argv)\n# noqa: S603 - argv is fixed words\n", 1), ("# ruff: noqa: S603 - argv is fixed words\nrun(argv)\n", 2)],
    ids=["noqa on another line", "file-level noqa"],
)
def test_a_noqa_away_from_noqa_row_is_red(text, line):
    """Sentence 5, cell 11: only the line at ruff's noqa_row can carry the noqa."""
    assert judged(text, record(line=line, noqa_line=line)) != []


@pytest.mark.parametrize(
    ("text", "is_red"),
    [
        ("run(\n    argv,\n)  # noqa: S603 - argv is fixed words\n", False),
        ("run(  # noqa: S603 - argv is fixed words\n    argv,\n)\n", True),
    ],
    ids=["noqa at noqa_row", "noqa at the finding's own line"],
)
def test_a_finding_spanning_lines_is_judged_at_noqa_row(text, is_red):
    """Sentence 5, cell 13: a finding inside a multi-line call is judged at noqa_row, not its own line."""
    assert bool(judged(text, record(line=1, noqa_line=3))) is is_red


@pytest.mark.parametrize("code", ["S101", "S105", "S106", "S107", "S603", "S607"])
@pytest.mark.parametrize(("in_tests", "is_red"), [(True, False), (False, True)], ids=["test file", "other file"])
def test_the_exempt_codes_are_green_only_in_a_test_file(code, in_tests, is_red):
    """Sentence 4, cells 7 and 8: S101, S105-S107, S603 and S607 are exempt in the files test-import judges alone."""
    finding = record(code=code, path="tests/test_a.py")
    test_files = frozenset({finding.path}) if in_tests else frozenset()
    assert bool(judged("x\n", finding, test_files)) is is_red


@pytest.mark.parametrize("code", ["S108", "S310"])
def test_a_shared_path_or_an_url_open_stays_red_in_a_test_file(code):
    """Sentence 4, cell 9: S108 and S310 are not exempt in tests."""
    finding = record(code=code, path="tests/test_a.py")
    assert judged("x\n", finding, frozenset({finding.path})) != []


@pytest.mark.parametrize(
    ("code", "refusing", "judging"),
    [("S603", python_lint.judge_security, python_lint.judge_print), ("T201", python_lint.judge_print, python_lint.judge_security)],
    ids=["security finding", "print finding"],
)
def test_a_file_tokenize_cannot_read_refuses_only_the_rule_whose_finding_lies_in_it(code, refusing, judging):
    """Sentence 5, cell 16, and #225 cell 12: a file ruff parsed but tokenize cannot is `cannot judge`, naming the
    file, for the rule whose finding lies in it; the other rule never reads its comments."""
    lint = linted([record(code=code)], {"src/p/a.py": "print(argv)\nx = '''open\n"})
    with pytest.raises(LintError, match="src/p/a.py"):
        refusing(lint)
    judging(lint)


@pytest.mark.parametrize(
    ("text", "is_red"),
    [
        (f"{PRINT}\n", True),
        (f"{PRINT}  # noqa: T201 - the door prints here\n", False),
        (f"{OWNER}\n{PRINT}\n", False),
        (f'"""Runs it."""\n\n{PRINT}\n{OWNER}\n', False),
        (f"{PRINT}\n# ruff: noqa: T201, T203 - the command line owns its output\n", False),
        (f"# ruff: noqa\n{PRINT}\n", True),
        (f"# ruff: noqa: T201\n{PRINT}\n", True),
        (f"# ruff: noqa: T201 - the command line, its output\n{PRINT}\n", True),
        (f"# ruff: noqa: S603, T201 - the command line owns its output\n{PRINT}\n", True),
        (f"# ruff: noqa: T203 - the command line owns its output\n{PRINT}\n", True),
        (f"# ruff: noqa: T201 - 42\n{PRINT}\n", True),
        (f"# ruff: noqa: T201: the command line owns its output\n{PRINT}\n", True),
        (f"x = 1  {OWNER}\n{PRINT}\n", True),
        (f"{OWNER}  # noqa: E501\n{PRINT}\n", True),
        (f"# flake8: noqa: T201 - the command line owns its output\n{PRINT}\n", True),
        (f'x = """\n{OWNER}\n"""\n{PRINT}\n', True),
    ],
    ids=["none", "line noqa", "owner on top", "owner below", "owner of both codes", "bare file noqa", "no reason",
         "comma in reason", "security code", "other code", "no letter in reason", "colon form", "after code",
         "second directive", "flake8 form", "inside a string"],
)
def test_only_an_own_line_reasoned_owner_naming_the_code_or_a_reasoned_line_noqa_clears_a_print(text, is_red):
    """#225 cells 4, 5 and 6: the owner is `# ruff: noqa: <T20 code>[, ...] - <reason>` on its own line, naming the
    code, with a letter and no comma in its reason; every other file directive leaves the print red."""
    findings, _ = judged_prints(text, [print_record(text)])
    assert any(PRINT_REASON in finding for finding in findings) is is_red, findings


def test_an_honoured_owner_is_reported_with_its_path_and_reason():
    """#225 cell 8: `python-print: output owner <path> - <reason>`, and no finding."""
    text = f"{OWNER}\n{PRINT}\n"
    assert judged_prints(text, [print_record(text)]) == (
        [], ("python-print: output owner src/p/a.py - the command line owns its output",)
    )


def test_a_fix_names_the_code_found_and_the_line_its_noqa_must_stand_on():
    text = "run(argv)\n" * 9
    (secured,) = python_lint.judge_security(linted([record(line=7, noqa_line=9)], {"src/p/a.py": text}))
    (other_secured,) = python_lint.judge_security(linted([record(code="S307")], {"src/p/a.py": text}))
    (printed,) = python_lint.judge_print(linted([record(code="T203")], {"src/p/a.py": text})).findings
    without_code = secured.fix.replace("S603", "")
    assert ("9" in without_code, "7" in without_code) == (True, False)
    assert ("S307" in other_secured.fix, "S603" in other_secured.fix) == (True, False)
    assert ("T203" in printed.fix, "T201" in printed.fix) == (True, False)


def test_an_owner_clears_only_its_own_files_prints_and_never_a_security_finding():
    """#225 cell 5: the owner's file is green for T201, another file stays red, and the owner's S findings are judged."""
    owner_text, other_text = f"{OWNER}\n{PRINT}\nrun(argv)\n", f"{PRINT}\n"
    owner_print, other_print = print_record(owner_text, "src/p/door.py"), print_record(other_text, "src/p/other.py")
    lint = linted(
        [owner_print, other_print, record(line=3, noqa_line=3, path="src/p/door.py")],
        {"src/p/door.py": owner_text, "src/p/other.py": other_text},
    )
    printed = [finding.location for finding in python_lint.judge_print(lint).findings]
    secured = [finding.location for finding in python_lint.judge_security(lint)]
    assert (printed, secured) == (["src/p/other.py:1"], ["src/p/door.py:3"])


def ruff_entry(code: str = "S307", filename: str = "/r/src/a.py", noqa_row: int | None = 1) -> dict:
    return {"code": code, "message": "m", "filename": filename, "location": {"row": 1, "column": 1}, "noqa_row": noqa_row}


@pytest.mark.parametrize(
    "report",
    ["not json", "{}", "[1]", json.dumps([{**ruff_entry(), "code": 7}]), json.dumps([{**ruff_entry(), "location": {}}]),
     json.dumps([ruff_entry(filename="/elsewhere/a.py")]), json.dumps([ruff_entry(noqa_row=None)]),
     json.dumps([ruff_entry(code="E501")])],
    ids=["not json", "not a list", "no record", "code not text", "no row", "unknown file", "no noqa_row", "code not selected"],
)
def test_a_report_quality_cannot_read_cannot_be_judged(report):
    """Sentence 8, cell 16: unreadable JSON is `cannot judge`, never clean."""
    with pytest.raises(LintError):
        python_lint.read_report(report, {"/r/src/a.py": "src/a.py"})


def test_a_syntax_error_is_unparsable_naming_the_file():
    """Sentence 8, cell 15: `invalid-syntax` names the file ruff cannot parse."""
    report = json.dumps([ruff_entry(), ruff_entry(code="invalid-syntax", filename="/r/src/b.py", noqa_row=None)])
    with pytest.raises(UnparsablePython, match="src/b.py"):
        python_lint.read_report(report, {"/r/src/a.py": "src/a.py", "/r/src/b.py": "src/b.py"})


@pytest.mark.integration
@pytest.mark.parametrize(
    "path",
    [
        "src/p/a.py", "scripts/run_it.py", "inner/tests/test_x.py", "src/p/with space.py", "-leading.py", ".hidden/a.py",
        "src/p/\udcff.py",
    ],
)
def test_every_tracked_regular_python_file_is_judged(build_repo, path):
    """Sentence 1, cell 1: any directory, a space, a leading `-` or a byte that is not UTF-8 in the name."""
    files = {"inner/pyproject.toml": "[project]\nname = 'x'\n", path: EVAL}
    assert flagged_paths(build_repo(files)) == [path]


@pytest.mark.integration
def test_untracked_linked_submodule_notebook_stub_and_suffixless_files_are_not_judged(build_repo):
    """Sentence 1, cells 2 and 3: none of these is judged, and the rule is clean."""
    repo = build_repo({"a.ipynb": EVAL, "a.pyi": EVAL, "scripts/run_it": EVAL, "real.txt": EVAL})
    (repo / "untracked.py").write_text(EVAL)
    (repo / "linked.py").symlink_to("real.txt")
    in_index(repo, "add", "linked.py")
    in_index(repo, "update-index", "--add", "--cacheinfo", f"160000,{'1' * 40},vendor")
    (repo / "vendor").mkdir()
    (repo / "vendor" / "inside.py").write_text(EVAL)
    assert flagged_paths(repo) == []


@pytest.mark.integration
def test_a_repository_without_tracked_python_is_clean_even_when_quality_holds_no_ruff_and_an_untracked_file_stays_unjudged(
    build_repo, capsys
):
    """Sentence 1, cell 3: with no tracked `.py` file nothing is judged and ruff is not asked for, the untracked file included."""
    repo = build_repo({"README.md": "x\n"})
    (repo / "untracked.py").write_text(EVAL)
    code, output = run(capsys, repo, nothing_installed)
    assert code == 0, output
    assert is_clean(output), output


@pytest.mark.integration
@pytest.mark.parametrize(
    "leave_out",
    [lambda repo: (repo / "src" / "p" / "a.py").unlink(), lambda repo: in_index(repo, "sparse-checkout", "set", "--no-cone", "/.env")],
    ids=["deleted", "sparse checkout"],
)
def test_a_tracked_python_file_the_checkout_does_not_hold_cannot_be_judged(build_repo, capsys, leave_out):
    """Sentence 1: every tracked `.py` file is read before ruff runs, so one the checkout lacks is refused, never
    certified clean by leaving it out; the other rules are still judged."""
    repo = build_repo({".env": "A=1\n", "src/p/a.py": EVAL})
    leave_out(repo)
    code, output = run(capsys, repo)
    assert code == 2, output
    assert f"{refusal_row('code')}cannot read src/p/a.py: the checkout does not hold it" in output
    assert env_row_stands(output)


@pytest.mark.integration
def test_more_files_than_one_run_holds_are_all_judged(build_repo, monkeypatch):
    """Sentence 1, cell 4: the file list is batched, and every batch is judged."""
    monkeypatch.setattr(python_lint, "ARGUMENT_BYTES_PER_RUN", 1)
    assert flagged_paths(build_repo({f"src/p/m{index}.py": EVAL for index in range(3)})) == [
        "src/p/m0.py", "src/p/m1.py", "src/p/m2.py",
    ]


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
    ("source", "fix_follows"),
    [
        (f"{PRINT}\n{PRINT}\n", [False, True]),
        (f"{PRINT}\nfrom pprint import pprint\npprint(1)\n", [True, True]),
        (f"{EVAL}{EVAL}", [True, True]),
        ('eval("1"); eval("2")\n', [False, True]),
    ],
    ids=["print twice", "print and pprint", "eval on two lines", "eval twice on one line"],
)
def test_equal_fixes_in_the_code_area_print_once(drive_code, source, fix_follows):
    run = drive_code({"src/p/a.py": source})
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
@pytest.mark.parametrize(
    "config",
    [
        {"pyproject.toml": '[tool.ruff.lint.per-file-ignores]\n"src/**" = ["S", "T201"]\n'},
        {"pyproject.toml": '[tool.ruff]\nexclude = ["src"]\n'},
        {"ruff.toml": 'extend = "missing.toml"\n'},
        {"ruff.toml": "this is no toml [\n"},
        {"ruff.toml": "[lint]\nignore = [\"S307\", \"T201\"]\n"},
    ],
    ids=["per-file-ignores", "exclude", "extend to a missing file", "invalid config", "ignore"],
)
@pytest.mark.parametrize(
    ("rule", "source"), [(Rule.PYTHON_SECURITY, EVAL), (Rule.PYTHON_PRINT, f"{PRINT}\n")], ids=["security", "print"]
)
def test_the_repositorys_own_ruff_settings_have_no_effect(build_repo, config, rule, source):
    """Sentence 3, cell 6, and #225 cell 3: the gate runs its own ruff, isolated from every config."""
    assert flagged_paths(build_repo({**config, "src/p/a.py": source}), rule) == ["src/p/a.py"]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("path", "is_red"),
    [("tests/test_a.py", False), ("tests/fixtures/a.py", True), ("src/p/a.py", True)],
    ids=["test file", "fixture", "source"],
)
def test_an_assert_is_exempt_only_in_the_test_files_test_import_judges(build_repo, path, is_red):
    """Sentence 4, cells 7 and 8: the boundary is test-import's, so `tests/fixtures/` stays red."""
    files = {"pyproject.toml": "[project]\nname = 'x'\n", path: "assert True\n"}
    assert bool(flagged_paths(build_repo(files))) is is_red


@pytest.mark.integration
def test_the_pinned_ruff_itself_honours_every_green_noqa_form_and_not_a_code_in_a_second_directive(tmp_path):
    """Sentence 5, cell 14: the canonical form is a subset of ruff's own grammar, and ruff reads only a line's first
    directive, so the gate keeps a code named only in the second one red."""
    names = []
    for index, line in enumerate([*GREEN_NOQA_LINES, CODE_IN_SECOND_DIRECTIVE]):
        names.append(f"m{index}.py")
        (tmp_path / names[-1]).write_text(f"import subprocess\n{line.replace('run(', 'subprocess.run(')}\n")

    def ruff(*options: str) -> subprocess.CompletedProcess:
        command = [installed_ruff(), "check", "--isolated", "--no-cache", "--select", "S603", *options, "--", *names]
        return subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, check=False)

    assert len(json.loads(ruff("--ignore-noqa", "--output-format", "json").stdout)) == len(names)
    honoured = ruff("--output-format", "json")
    reported = [Path(entry["filename"]).name for entry in json.loads(honoured.stdout)]
    assert (honoured.returncode, reported, honoured.stderr) == (1, [names[-1]], "")


@pytest.mark.integration
def test_the_pinned_ruff_itself_honours_the_owner_form(tmp_path):
    """#225 cell 5: ruff reads the owner directive below a docstring as a file-level exemption of the code it names."""
    (tmp_path / "door.py").write_text(f'"""Runs it."""\n\n{OWNER}\n{PRINT}\n')

    def ruff(*options: str) -> subprocess.CompletedProcess:
        command = [installed_ruff(), "check", "--isolated", "--no-cache", "--select", "T20", "--output-format", "json", *options, "--", "door.py"]
        return subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, check=False)

    assert len(json.loads(ruff("--ignore-noqa").stdout)) == 1
    honoured = ruff()
    assert (honoured.returncode, json.loads(honoured.stdout), honoured.stderr) == (0, [], "")


@pytest.mark.integration
def test_an_owner_stays_in_the_prelude_and_its_file_keeps_its_security_row(drive_code):
    run = drive_code({"src/p/door.py": f"{OWNER}\n{PRINT}\n{EVAL}", "src/p/other.py": f"{PRINT}\n"})
    rows = [(location, row.event) for location, row in area_finding_rows(run.out, Area.CODE)]
    assert run.out.splitlines()[0] == hygiene_run(run.repo, run.locate_ruff).reports[0]
    assert rows == [("src/p/door.py:3", FindingEvent.INSECURE), ("src/p/other.py:1", FindingEvent.PRINT_CALL)]
    assert (run.code, run.err) == (1, "")


@pytest.mark.integration
def test_a_syntax_error_is_unparsable_and_the_other_rules_are_still_judged(build_repo, capsys):
    """Sentence 8, cell 15, and #225 cell 10: both rules are unparsable, naming the file, exit 2."""
    code, output = run(capsys, build_repo({".env": "A=1\n", "src/p/bad.py": "def broken(:\n"}))
    assert code == 2
    assert f"{refusal_row('code')}ruff cannot parse src/p/bad.py" in output
    assert f"{refusal_row('code')}ruff cannot parse src/p/bad.py" in output
    assert env_row_stands(output)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("on_check", "says"),
    [
        ("echo boom >&2; exit 2", "ruff exited 2"),
        ("echo 'not json'", "ruff printed a report quality cannot read"),
        ("echo 'warning: Failed to lint src/p/a.py: gone' >&2; echo '[]'", "warning: Failed to lint src/p/a.py"),
        ("exec sleep 5", "did not finish within"),
    ],
    ids=["exit 2", "unreadable JSON", "failed to lint", "timeout"],
)
def test_a_ruff_run_quality_cannot_trust_cannot_be_judged(build_repo, capsys, monkeypatch, elsewhere, on_check, says):
    """Sentence 8, cell 16, and #225 cell 11: both rules cannot judge, exit 2, and the other rules are still judged."""
    repo = build_repo({".env": "A=1\n", "src/p/a.py": "x = 1\n"})
    binary = fake_ruff(elsewhere, on_check)
    monkeypatch.setattr(python_lint, "RUN_TIMEOUT_SECONDS", 0.5)
    code, output = run(capsys, repo, lambda: str(binary))
    rules = [Rule.PYTHON_SECURITY, Rule.PYTHON_PRINT]
    assert (code, list(refusals(repo, lambda: str(binary))), says in output) == (2, rules, True), output
    assert env_row_stands(output)


@pytest.mark.integration
@pytest.mark.parametrize("case", ["not installed", "recorded file deleted", "another version", "failing version", "unpinned"])
def test_a_ruff_quality_cannot_find_or_trust_names_the_reinstall_command(build_repo, capsys, monkeypatch, elsewhere, case):
    """#248 rule 4: a ruff that is missing, at another version or unable to report one makes both python rules cannot
    judge and names the reinstall command; a pin quality's own metadata lacks names the defect and no command."""
    repo = build_repo({".env": "A=1\n", "src/p/a.py": "x = 1\n"})
    (elsewhere / "failing").mkdir()
    failing_version = elsewhere / "failing" / "ruff"
    failing_version.write_text("#!/bin/sh\nexit 1\n")
    failing_version.chmod(0o755)
    locate = {
        "not installed": nothing_installed,
        "recorded file deleted": lambda: str(elsewhere / "gone" / "ruff"),
        "another version": lambda: str(fake_ruff(elsewhere, "echo '[]'", "0.0.1")),
        "failing version": lambda: str(failing_version),
        "unpinned": installed_ruff,
    }[case]
    if case == "unpinned":
        loosen_requirement(elsewhere / "metadata", monkeypatch, "ruff")
    code, output = run(capsys, repo, locate)
    assert code == 2, output
    assert list(refusals(repo, locate)) == [Rule.PYTHON_SECURITY, Rule.PYTHON_PRINT]
    assert env_row_stands(output)
    lines = [line.strip() for line in output.splitlines()]
    expected_once = int(case != "unpinned")
    for choice in scanners.REINSTALL.choices:
        assert (lines.count(choice.text), output.count(choice.text)) == (expected_once, expected_once), output
    if case == "unpinned":
        assert str(installation.UnpinnedDependency("ruff")) in output
    if case == "failing version":
        assert " ruff version exited 1" in output
    if case == "another version":
        assert f"ruff 0.0.1 found, the release pins {installation.required_version('ruff')}" in output


@pytest.mark.integration
def test_a_print_in_a_file_tokenize_cannot_read_refuses_only_python_print(build_repo, capsys, elsewhere):
    """#225 cell 12: ruff reports a print where the tokenizer fails; python-print cannot judge, python-security is judged."""
    repo = build_repo({"src/p/a.py": "print(argv)\nx = '''open\n"})
    entry = {**ruff_entry(code="T201", filename=os.path.realpath(repo / "src" / "p" / "a.py")), "message": "`print` found"}
    (elsewhere / "report.json").write_text(json.dumps([entry]))
    binary = fake_ruff(elsewhere, f"cat {elsewhere / 'report.json'}")
    code, output = run(capsys, repo, lambda: str(binary))
    assert (code, f"{refusal_row('code')}cannot read the comments of src/p/a.py" in output) == (2, True), output
    assert list(refusals(repo, lambda: str(binary))) == [Rule.PYTHON_PRINT], output


@pytest.mark.integration
def test_a_python_file_that_is_not_utf8_cannot_be_judged(build_repo, capsys):
    """Sentence 8, cell 16: ruff would skip it with a warning, so quality reads it first."""
    repo = build_repo({"src/p/a.py": "x = 1\n"})
    (repo / "src" / "p" / "a.py").write_bytes(b"x = '\xff'\n")
    code, output = run(capsys, repo)
    assert (code, f"{refusal_row('code')}cannot read src/p/a.py" in output) == (2, True), output


@pytest.mark.integration
def test_code_a_cookie_hides_from_ruff_cannot_be_judged(build_repo, capsys):
    """Sentence 8, cell 16: Python decodes this UTF-8-valid file with its utf-7 cookie to a second line holding an
    `eval`, ruff reads a comment; judging it clean would fail open."""
    repo = build_repo({"src/p/a.py": "x = 1\n"})
    (repo / "src" / "p" / "a.py").write_bytes(b'# coding: utf-7\nx = 1 # +AAo-y = eval("1")\n')
    code, output = run(capsys, repo)
    assert (code, f"{refusal_row('code')}cannot read src/p/a.py" in output) == (2, True), output


@pytest.mark.integration
def test_the_run_writes_nothing_and_never_uses_a_ruff_on_path(build_repo, monkeypatch, elsewhere):
    """Invariant, cell 21: no `.ruff_cache` in the checkout, and a ruff on PATH never runs."""
    repo = build_repo({"src/p/a.py": EVAL})
    marker = elsewhere / "ran"
    fake_ruff(elsewhere, f"touch {marker}")
    monkeypatch.setenv("PATH", f"{elsewhere}:{os.environ['PATH']}")
    assert flagged_paths(repo) == ["src/p/a.py"]
    status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--ignored"], capture_output=True, text=True, check=True)
    assert (status.stdout, marker.exists()) == ("", False)


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
    assert ((row.glyph, row.event), run.code) == ((Glyph.COULD_NOT_CHECK, cli.AreaEvent.NOT_CHECKED), 2)
    assert_block_prints(area_block(run.out, Area.CODE), run.area.findings, ())
    assert run.err == f"error: {cli.refusal_error(run.areas)}\nnext: {cli.REFUSED_AREAS_NEXT.text}\n"


@pytest.mark.integration
def test_a_file_that_opens_with_a_byte_order_mark_is_judged(drive_code):
    run = drive_code({"src/p/a.py": f"\ufeff{PRINT}\n"})
    (finding,) = run.area.findings
    assert (finding.location, run.code, run.err) == ("src/p/a.py:1", 1, "")
