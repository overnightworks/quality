import os
from pathlib import Path

import pytest

from quality import twins
from quality.report import NoteEvent
from quality.rules import Area, Rule
from tests.support.baselines import DEBT_BASELINE_STATES
from tests.support.check import check_output, check_result, note_rows, refusals, rule_notes, runs_apart_by_the_note
from tests.support.twins import helper, twin_test

BASELINE_PATH = "quality/baseline.toml"


def counts(files: dict[str, str]) -> tuple[int, int]:
    """The groups of test twins and of copied helpers among `files`."""
    found = twins.count_twins({path: text.encode() for path, text in files.items()})
    return found.tests, found.helpers


def joined(*sources: str) -> str:
    return "\n\n".join(sources)


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ({"tests/test_a.py": joined(twin_test("test_one", "a"), twin_test("test_two", "x"))}, 1),
        ({"tests/test_a.py": joined(twin_test("test_one", 1), twin_test("test_two", 2), twin_test("test_three", 3))}, 1),
        ({"tests/test_a.py": joined(twin_test("test_one", 1), twin_test("test_two", 2), twin_test("test_three", 3, passes=4), twin_test("test_four", 3, passes=4))}, 2),
        ({"tests/test_a.py": joined(twin_test("test_one", 1, passes=2), twin_test("test_two", 2, passes=2))}, 0),
        ({"tests/test_a.py": joined(twin_test("test_one"), twin_test("test_two").replace("run", "other"))}, 0),
        ({"tests/test_a.py": twin_test("test_one"), "tests/test_b.py": twin_test("test_two")}, 0),
        ({"src/a.py": joined(twin_test("test_one"), twin_test("test_two"))}, 0),
        ({"tests/a.ts": joined(twin_test("test_one"), twin_test("test_two"))}, 0),
        ({"tests/test_a.py": joined(twin_test("check_one"), twin_test("check_two"))}, 0),
        ({"tests/test_a.py": joined(twin_test("test_one", decorator="@one\n"), twin_test("test_two", decorator="@two\n"))}, 1),
        ({"tests/test_a.py": "class TestA:\n" + "".join(f"    {line}\n" for line in joined(twin_test("test_one"), twin_test("test_two")).splitlines())}, 1),
        ({"tests/test_a.py": "def test_outer():\n" + "".join(f"    {line}\n" for line in joined(twin_test("test_one"), twin_test("test_two")).splitlines())}, 1),
        ({"tests/test_a.py": joined(twin_test("test_one"), twin_test("test_two")), "tests/test_b.py": "def broken(:\n"}, 1),
        ({"tests/test_a.py": joined(twin_test("test_one").replace("def", "async def"), twin_test("test_two").replace("def", "async def"))}, 1),
    ],
    ids=[
        "two tests that differ in a literal are a group",
        "three twins are one group",
        "two separate twin pairs are two groups",
        "a test of 11 tokens is below the floor",
        "tests that call different names are no twins",
        "tests in two files are no test twins",
        "a function of a non-test file is no test",
        "a file of another language under tests is skipped",
        "a function not named test is no test",
        "decorators are not compared",
        "a method of a class is a test",
        "a nested test is a test",
        "a file Python cannot parse is skipped",
        "an async test is a test",
    ],
)
def test_same_file_tests_equal_once_literals_are_folded_form_one_group(files, expected):
    """TWIN-1, TWIN-2: the unit of the test count is the group, from 12 tokens, names kept and literals folded."""
    assert counts(files)[0] == expected


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ({"tests/test_a.py": helper("first"), "tests/test_b.py": helper("second")}, 1),
        ({"tests/test_a.py": helper("first"), "tests/test_b.py": helper("second"), "tests/test_c.py": helper("third")}, 1),
        ({"tests/test_a.py": helper("first"), "tests/test_b.py": helper("second", "other")}, 0),
        ({"tests/test_a.py": helper("first", passes=0), "tests/test_b.py": helper("second", passes=0)}, 0),
        ({"tests/test_a.py": joined(helper("first"), helper("second"))}, 0),
        ({"tests/test_a.py": helper("first"), "src/b.py": helper("second")}, 0),
        ({"tests/conftest.py": helper("first"), "tests/support/b.py": helper("second")}, 1),
        ({"tests/test_a.py": twin_test("test_one"), "tests/test_b.py": twin_test("test_two")}, 0),
    ],
    ids=[
        "a helper copied into a second file is a group",
        "three files holding one helper are one group",
        "a helper that returns another constant is no copy",
        "a helper of 7 tokens is below the floor",
        "a helper twice in one file is no copy",
        "a helper of a non-test file is no helper",
        "a conftest and a support module are test files",
        "a test is no helper",
    ],
)
def test_helpers_equal_with_names_and_literals_kept_in_two_files_form_one_group(files, expected):
    """TWIN-1, TWIN-3: the unit of the helper count is the group, from 8 tokens, whatever the functions are called."""
    _, helper_groups = counts(files)
    assert helper_groups == expected


@pytest.mark.parametrize(
    ("files", "note"),
    [
        (
            {"tests/test_a.py": joined(twin_test("test_one", 1), twin_test("test_two", 2)), "tests/b.py": helper("a"), "tests/c.py": helper("b")},
            "1 group of test twins · 1 group of copied test helpers",
        ),
        ({"tests/test_a.py": joined(twin_test("test_one", 1), twin_test("test_two", 2))}, "1 group of test twins"),
        ({"tests/b.py": helper("a"), "tests/c.py": helper("b")}, "1 group of copied test helpers"),
        (
            {"tests/test_a.py": joined(*[twin_test(f"test_{n}", n) for n in range(2)], *[twin_test(f"test_x{n}", n, passes=5) for n in range(2)])},
            "2 groups of test twins",
        ),
    ],
    ids=["both", "twins only", "copies only", "plural"],
)
def test_the_debt_area_gets_one_open_note_at_the_repository(build_repo, capsys, files, note):
    """TWIN-4: a count of zero leaves its half out."""
    repo = build_repo(files)
    (found,) = rule_notes(repo, Rule.TEST_TWIN)
    _, output = check_output(capsys, repo)
    assert (found.path, found.line, found.event, found.fact) == (".", None, NoteEvent.OPEN, note)
    assert [(row.event, row.fact) for row in note_rows(output, ".")] == [("open", note)]


def test_no_note_prints_when_both_counts_are_zero(build_repo):
    """TWIN-4."""
    assert rule_notes(build_repo({"tests/test_a.py": twin_test("test_one")}), Rule.TEST_TWIN) == []


def test_the_rule_stands_in_the_debt_area_after_the_job_timeouts_and_only_reports(build_repo):
    """TWIN-4, TWIN-6: the rule's result holds no finding and is not measured only; its release switch is report-only."""
    result = check_result(build_repo({"tests/test_a.py": joined(twin_test("test_one"), twin_test("test_two"))}))
    debt = next(area for area in result.areas if area.area is Area.DEBT)
    verdict = debt.verdicts[-2]
    assert (verdict.rule, verdict.findings, verdict.measured_only, verdict.extent) == (Rule.TEST_TWIN, (), False, 0)
    assert [verdict.rule for verdict in debt.verdicts] == [Rule.SIZE, Rule.CLONE, Rule.JOB_TIMEOUT, Rule.TEST_TWIN, Rule.SPEC_CITATION]


@pytest.mark.integration
@pytest.mark.parametrize("state", DEBT_BASELINE_STATES, ids=list(DEBT_BASELINE_STATES))
def test_every_other_line_and_the_exit_stay_as_the_same_run_without_the_rule(tmp_path, capsys, state):
    """TWIN-4: the note is the only line the rule adds, in every state of the baseline. The second repository has the same
    size and clone rows, because those streams fold names and literals, and no twin or copied helper."""
    twins_files = {"tests/test_a.py": joined(twin_test("test_one"), twin_test("test_two")), "tests/b.py": helper("a"), "tests/c.py": helper("b")}
    plain_files = {
        "tests/test_a.py": joined(twin_test("test_one"), twin_test("test_two").replace("run", "other")),
        "tests/b.py": helper("a"),
        "tests/c.py": helper("b", literal=2),
    }
    with_rule, without_rule = runs_apart_by_the_note(tmp_path, capsys, state, twins_files, plain_files, "test twins")
    assert with_rule == without_rule


def test_a_baseline_that_cannot_be_read_refuses_size_and_clone_and_the_note_still_prints(build_repo):
    """TWIN-5, CLONE-9."""
    repo = build_repo({"tests/test_a.py": joined(twin_test("test_one"), twin_test("test_two")), BASELINE_PATH: "[size\n"})
    assert set(refusals(repo)) == {Rule.SIZE, Rule.CLONE, Rule.JOB_TIMEOUT}
    assert len(rule_notes(repo, Rule.TEST_TWIN)) == 1


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads every file")
def test_sources_that_cannot_be_read_refuse_the_area_and_print_no_note(build_repo):
    """TWIN-5."""
    repo = build_repo({"tests/test_a.py": joined(twin_test("test_one"), twin_test("test_two"))})
    Path(repo, "tests/test_a.py").chmod(0)
    assert Rule.TEST_TWIN in refusals(repo)
    assert rule_notes(repo, Rule.TEST_TWIN) == []
