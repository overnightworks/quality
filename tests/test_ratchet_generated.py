"""#174: a file `quality/config.toml` declares generated leaves the size rule and the baseline write."""

import pytest

from quality import cli
from quality.config import CONFIG_PATH
from quality.ratchet import BASELINE_PATH
from tests.support.check import hygiene_output as hygiene
from tests.support.generated_files import MANDATORY_JOB, TYPES_FILE, generated_config
from tests.support.source_code import long_function, typescript_file

GATE = f'[gate]\nmandatory_jobs = ["{MANDATORY_JOB}"]\n'
STALE_FINDING = (
    f"size {TYPES_FILE} - declared in quality/config.toml with [[size.generated]], but no tracked file the size rule "
    "measures lies there."
)
STALE_FIX = "Fix: delete its [[size.generated]] entry from quality/config.toml."


def size_lines(output: str) -> list[str]:
    return [line for line in output.splitlines() if line.startswith("size ")]


@pytest.mark.parametrize(
    ("files", "declared", "baseline", "expected"),
    [
        ({TYPES_FILE: typescript_file(900)}, True, "", []),
        ({TYPES_FILE: typescript_file(900)}, False, "", [f"size {TYPES_FILE} - file has 900 lines; debt starts at 800 lines (new debt)."]),
        ({TYPES_FILE: typescript_file(900)}, True, f'[file_lines]\n"{TYPES_FILE}" = 850\n', []),
        ({"gen/api.py": long_function(70)}, True, "", []),
        ({"gen/api.py": long_function(70)}, False, "", ["size gen/api.py:1 - function long_one has 70 lines; debt starts at 60 lines (new debt)."]),
    ],
    ids=["a declared TypeScript file", "the same file undeclared", "grown past its leftover entry", "a declared Python function", "undeclared Python function"],
)
def test_a_declared_file_is_clean_at_any_size_and_the_same_file_undeclared_is_debt(build_repo, capsys, files, declared, baseline, expected):
    """#174 sentence 1, acceptance 1: lines, functions and a leftover baseline entry of a declared file are not judged."""
    path = next(iter(files))
    config = GATE + (generated_config(path=path) if declared else "")
    _, output = hygiene(capsys, build_repo({**files, CONFIG_PATH: config, BASELINE_PATH: baseline}))
    assert [line.split(" Fix")[0] for line in size_lines(output)] == expected


@pytest.mark.parametrize("declared", [True, False], ids=["declared", "undeclared"])
def test_the_report_only_counts_leave_a_declared_file_out(build_repo, capsys, declared):
    """#174 sentence 1: without a baseline the counted files are those the size rule judges."""
    config = GATE + (generated_config() if declared else "")
    _, output = hygiene(capsys, build_repo({TYPES_FILE: typescript_file(900), CONFIG_PATH: config}))
    counted = 0 if declared else 1
    assert f"production: {counted} file(s) >= 800 lines" in output


@pytest.mark.parametrize("baseline", [{BASELINE_PATH: ""}, {}], ids=["with a baseline", "without a baseline"])
@pytest.mark.parametrize(
    ("files", "arrange"),
    [
        ({"src/a.py": "x = 1\n"}, None),
        ({"src/a.py": "x = 1\n", TYPES_FILE.replace(".ts", ".txt"): "text\n"}, "tracked, no measured language"),
        ({"src/a.py": "x = 1\n", TYPES_FILE: "export {};\n"}, "deleted from disk"),
    ],
    ids=["absent", "tracked in no measured language", "deleted from disk"],
)
def test_a_declaration_without_a_measured_file_is_one_size_finding_with_the_delete_fix(build_repo, capsys, files, arrange, baseline):
    """#174 sentence 4, acceptance 4: a stale declaration is a size finding on its path, in both report modes."""
    declared = TYPES_FILE.replace(".ts", ".txt") if arrange == "tracked, no measured language" else TYPES_FILE
    repo = build_repo({**files, CONFIG_PATH: GATE + generated_config(path=declared), **baseline})
    if arrange == "deleted from disk":
        (repo / TYPES_FILE).unlink()
    code, output = hygiene(capsys, repo)
    assert (code, size_lines(output)) == (1, [STALE_FINDING.replace(TYPES_FILE, declared)])
    assert STALE_FIX in output


def test_a_declared_python_file_python_cannot_parse_leaves_the_size_rule_judgeable_but_not_the_baseline_write(build_repo, capsys):
    """#174 Design: the size rule leaves a declared file's unparsable entry out; the baseline write still refuses
    a tree whose clones it could not measure."""
    repo = build_repo({"gen/api.py": "def broken(:\n", CONFIG_PATH: GATE + generated_config(path="gen/api.py"), BASELINE_PATH: ""})
    _, output = hygiene(capsys, repo)
    assert size_lines(output) == []
    assert "size unparsable" not in output
    assert cli.main(["baseline", "write", str(repo)]) == 2
    assert "baseline: not written - gen/api.py is not valid Python" in capsys.readouterr().err


def test_baseline_write_leaves_a_declared_file_out_and_names_no_raise_for_it(build_repo, capsys):
    """#174 sentence 1, acceptance 2: the written baseline holds nothing of the declared file, and its count line
    counts only what was written."""
    files = {TYPES_FILE: typescript_file(900), "gen/api.py": long_function(70), CONFIG_PATH: GATE + generated_config(path="gen/api.py") + generated_config(path=TYPES_FILE)}
    repo = build_repo({**files, BASELINE_PATH: f'[file_lines]\n"{TYPES_FILE}" = 850\n'})
    assert cli.main(["baseline", "write", str(repo)]) == 0
    output = capsys.readouterr().out
    assert "raises" not in output
    assert "adds" not in output
    assert "baseline: wrote 0 entries and 0 clone pairs" in output
    assert TYPES_FILE not in (repo / BASELINE_PATH).read_text()
