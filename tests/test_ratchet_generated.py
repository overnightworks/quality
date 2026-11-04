"""A file `quality/config.toml` declares generated leaves the size rule and the fix."""

import pytest

from quality.config import CONFIG_PATH
from quality.ratchet import BASELINE_PATH, load_baseline
from quality.report import FindingEvent
from quality.rules import Rule
from tests.support.check import (
    area_fact,
    area_finding_rows,
    assert_finding_vocabulary,
    assert_row_prints_finding,
    leading_count,
    recorded_entries,
    refusals,
    rows_of,
    rule_findings,
    rule_notes,
)
from tests.support.check import check_output as run
from tests.support.door import FIX_WORDS, door_in
from tests.support.generated_files import MANDATORY_JOB, TYPES_FILE, generated_config
from tests.support.source_code import long_function, typescript_file

GATE = f'[gate]\nmandatory_jobs = ["{MANDATORY_JOB}"]\n'


@pytest.mark.parametrize(
    ("files", "declared", "baseline", "expected"),
    [
        ({TYPES_FILE: typescript_file(900)}, True, "", []),
        ({TYPES_FILE: typescript_file(900)}, False, "", [(TYPES_FILE, FindingEvent.NEW_DEBT)]),
        ({TYPES_FILE: typescript_file(900)}, True, f'[file_lines]\n"{TYPES_FILE}" = 850\n', [(TYPES_FILE, FindingEvent.CAN_FALL)]),
        ({"gen/api.py": long_function(70)}, True, "", []),
        ({"gen/api.py": long_function(70)}, False, "", [("gen/api.py:1", FindingEvent.NEW_DEBT)]),
    ],
    ids=["a declared TypeScript file", "the same file undeclared", "grown past its leftover entry", "a declared Python function", "undeclared Python function"],
)
def test_a_declared_file_is_clean_at_any_size_and_the_same_file_undeclared_is_debt(build_repo, capsys, files, declared, baseline, expected):
    """#174 sentence 1, acceptance 1: lines, functions and a leftover baseline entry of a declared file are not judged."""
    path = next(iter(files))
    config = GATE + (generated_config(path=path) if declared else "")
    repo = build_repo({**files, CONFIG_PATH: config, BASELINE_PATH: baseline})
    _, output = run(capsys, repo)
    assert [(finding.location, finding.event) for finding in rule_findings(repo, Rule.SIZE)] == expected
    assert [(location, row.event) for location, row in area_finding_rows(output, "debt")] == expected


@pytest.mark.parametrize("declared", [True, False], ids=["declared", "undeclared"])
def test_the_not_recorded_count_leaves_a_declared_file_out(build_repo, capsys, declared):
    """#174 sentence 1: without a baseline the counted entries are those the size rule judges, and a recording writes."""
    config = GATE + (generated_config() if declared else "")
    repo = build_repo({TYPES_FILE: typescript_file(900), CONFIG_PATH: config})
    _, output = run(capsys, repo)
    assert leading_count(area_fact(output, "debt")) == recorded_entries(repo) == (0 if declared else 1)


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
    code, output = run(capsys, repo)
    (finding,) = rule_findings(repo, Rule.SIZE)
    assert (code, finding.location, finding.event) == (1, declared, FindingEvent.UNUSED), output
    assert_finding_vocabulary(finding)
    (row,) = rows_of(output, finding)
    assert_row_prints_finding(row, finding)
    (repo / CONFIG_PATH).write_text(GATE, encoding="utf-8")
    assert rule_findings(repo, Rule.SIZE) == []


def test_a_declared_python_file_python_cannot_parse_leaves_the_size_rule_judgeable_but_not_the_fix(build_repo, capsys):
    """The size rule leaves a declared file's unparsable entry out; the fix still refuses a tree whose clones it could not measure."""
    seeded = '[clones."src/a.py"]\nlong_one = [["src/b.py", "long_one"]]\n'
    repo = build_repo({"gen/api.py": "def broken(:\n", CONFIG_PATH: GATE + generated_config(path="gen/api.py"), BASELINE_PATH: seeded})
    seeded_bytes = (repo / BASELINE_PATH).read_bytes()
    _, output = run(capsys, repo)
    assert rule_findings(repo, Rule.SIZE) == []
    assert area_finding_rows(output, "debt") == []
    assert Rule.SIZE not in refusals(repo)
    capsys.readouterr()
    assert door_in(repo, *FIX_WORDS) == 2
    assert "  ✗ debt          not checked    gen/api.py is not valid Python" in capsys.readouterr().out
    assert (repo / BASELINE_PATH).read_bytes() == seeded_bytes


def test_a_declared_file_python_cannot_parse_is_no_note_without_a_baseline(build_repo):
    """#290 O22 (b): the size rule leaves a declared file's unparsable entry out, so no note names it."""
    repo = build_repo({"gen/api.py": "def broken(:\n", CONFIG_PATH: GATE + generated_config(path="gen/api.py")})
    assert rule_notes(repo, Rule.SIZE) == []


def test_a_fix_drops_the_entry_of_a_declared_file(build_repo, capsys):
    """A declared file is left out of the measurement, so its held entry can fall and the fix drops it."""
    files = {TYPES_FILE: typescript_file(900), "gen/api.py": long_function(70), CONFIG_PATH: GATE + generated_config(path="gen/api.py") + generated_config(path=TYPES_FILE)}
    repo = build_repo({**files, BASELINE_PATH: f'[file_lines]\n"{TYPES_FILE}" = 850\n'})
    assert door_in(repo, *FIX_WORDS) == 0
    capsys.readouterr()
    assert load_baseline((repo / BASELINE_PATH).read_text()).size == {}
