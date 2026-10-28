import json
import os
import subprocess
from pathlib import Path

import pytest
from ruff import find_ruff_bin

from quality import hygiene, python_lint
from quality.python_lint import LintError, RuffFinding, UnparsablePython
from tests.support.hygiene import hygiene_output as run
from tests.support.layout import in_index

EVAL = 'eval("1")\n'
EVAL_REASON = "S307 Use of possibly insecure function; consider using `ast.literal_eval`"
GREEN_NOQA_LINES = [
    "run(argv)  # noqa: S603 - argv is fixed words",
    "run(argv)  # noqa: S603, S607 - argv is fixed words",
    "run(argv)  # noqa: S603 - argv is fixed words  # a closing remark",
]


def record(code: str = "S603", line: int = 1, noqa_line: int = 1, path: str = "src/p/a.py") -> RuffFinding:
    return RuffFinding(path, code, "`subprocess` call: check for execution of untrusted input", line, noqa_line)


def judged(text: str, finding: RuffFinding, test_files: frozenset[str] = frozenset()) -> list[str]:
    return [f"{item.location} {item.reason}" for item in python_lint.judge([finding], {finding.path: text}, test_files)]


def security_verdict(repo: Path) -> hygiene.RuleVerdict:
    return next(verdict for verdict in hygiene.run_hygiene(repo).verdicts if verdict.rule == python_lint.RULE)


def flagged_paths(repo: Path) -> list[str]:
    verdict = security_verdict(repo)
    assert verdict.refusal is None, verdict.refusal
    return [finding.path for finding in verdict.findings]


def fake_ruff(directory: Path, on_check: str, version: str = "0.17.1") -> Path:
    """A ruff that reports `version` and runs `on_check` (shell) for any other call."""
    binary = directory / "ruff"
    binary.write_text(f'#!/bin/sh\nif [ "$1" = "--version" ]; then echo "ruff {version}"; exit 0; fi\n{on_check}\n')
    binary.chmod(0o755)
    return binary


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
        'run("# noqa: S603 - argv is fixed words")',
    ],
    ids=["none", "no reason", "blanket", "type ignore as reason", "pragma as reason", "colon form", "comma in reason",
         "wrong code", "no letter in reason", "no space after colon", "second bare directive", "inside a string"],
)
def test_every_other_noqa_form_is_red(line):
    """Sentence 5, cells 11 and 12: only the canonical form is green; a bare second directive is not hidden."""
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


@pytest.mark.parametrize("code", sorted(python_lint.TEST_EXEMPT_CODES))
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


def test_a_file_tokenize_cannot_read_cannot_be_judged():
    """Sentence 5, cell 16: a file ruff parsed but tokenize cannot is `cannot judge`, naming the file."""
    with pytest.raises(LintError, match="src/p/a.py"):
        python_lint.judge([record()], {"src/p/a.py": "run(argv)\nx = '''open\n"}, frozenset())


def ruff_entry(code: str = "S307", filename: str = "/r/src/a.py", noqa_row: int | None = 1) -> dict:
    return {"code": code, "message": "m", "filename": filename, "location": {"row": 1, "column": 1}, "noqa_row": noqa_row}


@pytest.mark.parametrize(
    "report",
    ["not json", "{}", "[1]", json.dumps([{**ruff_entry(), "code": 7}]), json.dumps([{**ruff_entry(), "location": {}}]),
     json.dumps([ruff_entry(filename="/elsewhere/a.py")]), json.dumps([ruff_entry(noqa_row=None)])],
    ids=["not json", "not a list", "no record", "code not text", "no row", "unknown file", "no noqa_row"],
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
def test_a_repository_without_tracked_python_never_starts_ruff(build_repo, monkeypatch):
    """Sentence 1, cell 3: `ruff check --` without paths would lint the working directory, so ruff is not started."""
    repo = build_repo({"README.md": "x\n"})
    (repo / "untracked.py").write_text(EVAL)

    def no_ruff() -> str:
        raise AssertionError("ruff started")

    monkeypatch.setattr(python_lint, "find_ruff_bin", no_ruff)
    assert flagged_paths(repo) == []


@pytest.mark.integration
def test_more_files_than_one_run_holds_are_all_judged(build_repo, monkeypatch):
    """Sentence 1, cell 4: the file list is batched, and every batch is judged."""
    monkeypatch.setattr(python_lint, "ARGUMENT_BYTES_PER_RUN", 1)
    assert flagged_paths(build_repo({f"src/p/m{index}.py": EVAL for index in range(3)})) == [
        "src/p/m0.py", "src/p/m1.py", "src/p/m2.py",
    ]


@pytest.mark.integration
def test_a_security_finding_names_its_line_code_and_message(build_repo, capsys):
    """Sentence 2, cell 5: `python-security <path>:<line> - <CODE> <message>`, exit 1."""
    code, output = run(capsys, build_repo({"src/p/a.py": f"x = 1\n{EVAL}"}))
    assert (code, f"python-security src/p/a.py:2 - {EVAL_REASON}" in output) == (1, True), output


@pytest.mark.integration
@pytest.mark.parametrize(
    "config",
    [
        {"pyproject.toml": '[tool.ruff.lint.per-file-ignores]\n"src/**" = ["S"]\n'},
        {"pyproject.toml": '[tool.ruff]\nexclude = ["src"]\n'},
        {"ruff.toml": 'extend = "missing.toml"\n'},
        {"ruff.toml": "this is no toml [\n"},
        {"ruff.toml": "[lint]\nignore = [\"S307\"]\n"},
    ],
    ids=["per-file-ignores", "exclude", "extend to a missing file", "invalid config", "ignore"],
)
def test_the_repositorys_own_ruff_settings_have_no_effect(build_repo, config):
    """Sentence 3, cell 6: the gate runs its own ruff, isolated from every config."""
    assert flagged_paths(build_repo({**config, "src/p/a.py": EVAL})) == ["src/p/a.py"]


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
def test_the_pinned_ruff_itself_honours_every_green_noqa_form(tmp_path):
    """Sentence 5, cell 14: the canonical form is a subset of ruff's own grammar."""
    names = []
    for index, line in enumerate(GREEN_NOQA_LINES):
        names.append(f"m{index}.py")
        (tmp_path / names[-1]).write_text(f"import subprocess\n{line.replace('run(', 'subprocess.run(')}\n")

    def ruff(*options: str) -> subprocess.CompletedProcess:
        command = [find_ruff_bin(), "check", "--isolated", "--no-cache", "--select", "S603", *options, "--", *names]
        return subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, check=False)

    assert len(json.loads(ruff("--ignore-noqa", "--output-format", "json").stdout)) == len(names)
    honoured = ruff("--output-format", "json")
    assert (honoured.returncode, honoured.stdout.strip(), honoured.stderr) == (0, "[]", "")


@pytest.mark.integration
def test_a_syntax_error_is_unparsable_and_the_other_rules_are_still_judged(build_repo, capsys):
    """Sentence 8, cell 15: `python-security unparsable` names the file, exit 2."""
    code, output = run(capsys, build_repo({".env": "A=1\n", "src/p/bad.py": "def broken(:\n"}))
    assert code == 2
    assert "hygiene: python-security unparsable - ruff cannot parse src/p/bad.py" in output
    assert "env-file .env - " in output


@pytest.mark.integration
@pytest.mark.parametrize(
    ("on_check", "version", "says"),
    [
        ("echo '[]'", "0.0.1", "reports ruff 0.0.1, quality pins ruff==0.17.1"),
        ("echo boom >&2; exit 2", "0.17.1", "ruff exited 2"),
        ("echo 'not json'", "0.17.1", "ruff printed a report quality cannot read"),
        ("echo 'warning: Failed to lint src/p/a.py: gone' >&2; echo '[]'", "0.17.1", "warning: Failed to lint src/p/a.py"),
        ("exec sleep 5", "0.17.1", "did not finish within"),
    ],
    ids=["version mismatch", "exit 2", "unreadable JSON", "failed to lint", "timeout"],
)
def test_a_ruff_run_quality_cannot_trust_cannot_be_judged(build_repo, capsys, monkeypatch, elsewhere, on_check, version, says):
    """Sentence 8, cell 16: the rule cannot judge, exit 2, and the other rules are still judged."""
    repo = build_repo({".env": "A=1\n", "src/p/a.py": "x = 1\n"})
    binary = fake_ruff(elsewhere, on_check, version)
    monkeypatch.setattr(python_lint, "find_ruff_bin", lambda: str(binary))
    monkeypatch.setattr(python_lint, "RUN_TIMEOUT_SECONDS", 0.5)
    code, output = run(capsys, repo)
    assert (code, "hygiene: python-security cannot judge - " in output, says in output) == (2, True, True), output
    assert "env-file .env - " in output


@pytest.mark.integration
def test_a_python_file_that_is_not_utf8_cannot_be_judged(build_repo, capsys):
    """Sentence 8, cell 16: ruff would skip it with a warning, so quality reads it first."""
    repo = build_repo({"src/p/a.py": "x = 1\n"})
    (repo / "src" / "p" / "a.py").write_bytes(b"x = '\xff'\n")
    code, output = run(capsys, repo)
    assert (code, "hygiene: python-security cannot judge - cannot read src/p/a.py" in output) == (2, True), output


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
