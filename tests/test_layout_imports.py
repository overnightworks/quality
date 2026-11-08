"""The test-import rule: no file under a Python project's `tests/` imports a test module or a conftest."""

import pytest

from quality.output import FIX_LABEL
from quality.report import FindingEvent
from quality.rules import Rule
from tests.support.check import check_output, refusal_row, refusals, rows_of, rule_findings
from tests.support.git_repositories import put
from tests.support.layout import CONFIG, GUIDANCE, assert_prints_as_kind, case, declaring, in_index, red
from tests.support.merge import ONE_JOB

RULE = Rule.TEST_IMPORT


def project(files: dict[str, str]) -> dict[str, str]:
    """A root project (its `pyproject.toml` is added) tracking `files`."""
    return {**GUIDANCE, "pyproject.toml": "", **files}


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        case("import conftest",
             {"tests/conftest.py": "", "tests/support/x.py": "import conftest\n"}, ["tests/support/x.py:1"]),
        case("from conftest",
             {"tests/conftest.py": "", "tests/support/x.py": "from conftest import f\n"}, ["tests/support/x.py:1"]),
        case("dotted test module",
             {"tests/test_a.py": "", "tests/support/x.py": "from tests.test_a import y\n"}, ["tests/support/x.py:1"]),
        case("dotted e2e conftest",
             {"tests/e2e/conftest.py": "", "tests/support/x.py": "import tests.e2e.conftest\n"},
             ["tests/support/x.py:1"]),
        case("a from-import of a conftest by name",
             {"tests/conftest.py": "", "tests/support/x.py": "from tests import conftest\n"}, ["tests/support/x.py:1"]),
        case("a name that is no module",
             {"tests/test_a.py": "", "tests/support/x.py": "from tests.support.x import test_helper\n"}, []),
        case("relative test module",
             {"tests/test_a.py": "", "tests/support/x.py": "from ..test_a import y\n"}, ["tests/support/x.py:1"]),
        case("relative conftest by name",
             {"tests/conftest.py": "", "tests/x.py": "from . import conftest\n"}, ["tests/x.py:1"]),
        case("relative conftest above",
             {"tests/conftest.py": "", "tests/support/x.py": "from ..conftest import z\n"}, ["tests/support/x.py:1"]),
        case("relative support module", {"tests/support/y.py": "", "tests/support/x.py": "from .y import z\n"}, []),
        case("a relative name matches only the exact path",
             {"tests/test_a.py": "", "tests/support/x.py": "from .test_a import y\n"}, []),
        case("bare test module",
             {"tests/test_x.py": "", "tests/support/y.py": "import test_x\n"}, ["tests/support/y.py:1"]),
        case("a third-party name", {"tests/test_x.py": "", "tests/support/y.py": "import test_utils\n"}, []),
        case("a pythonpath spelling",
             {"tests/test_rule_option.py": "", "tests/support/y.py": "import test_rule_option\n"},
             ["tests/support/y.py:1"]),
        case("an e2e-rooted spelling",
             {"tests/e2e/conftest.py": "", "tests/support/y.py": "from e2e.conftest import x\n"},
             ["tests/support/y.py:1"]),
        case("a suffix aligns by component",
             {"tests/conftest.py": "", "tests/support/y.py": "import myconftest\n"}, []),
        case("an e2e support module",
             {"tests/e2e/support/x.py": "", "tests/e2e/prove.py": "import tests.e2e.support.x\n"}, []),
        case("a sibling driver",
             {"tests/e2e/oidc/prove.py": "", "tests/e2e/oidc/run.py": "import prove\nfrom . import prove\n"}, []),
        case("an e2e driver importing a conftest",
             {"tests/conftest.py": "", "tests/e2e/prove.py": "import conftest\n"}, ["tests/e2e/prove.py:1"]),
        case("an e2e conftest importing a test module",
             {"tests/test_a.py": "", "tests/e2e/conftest.py": "import test_a\n"}, ["tests/e2e/conftest.py:1"]),
        case("inside a function",
             {"tests/conftest.py": "", "tests/support/y.py": "def f():\n    import conftest\n"},
             ["tests/support/y.py:2"]),
        case("under TYPE_CHECKING",
             {"tests/conftest.py": "", "tests/support/y.py": "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import conftest\n"},
             ["tests/support/y.py:3"]),
        case("a fixture file is not judged",
             {"tests/conftest.py": "", "tests/fixtures/s/m.py": "import conftest\n"}, []),
        case("a fixture file is no target",
             {"tests/fixtures/s/test_a.py": "", "tests/support/y.py": "import test_a\n"}, []),
        case("a project without src",
             {"tests/conftest.py": "", "tests/support/y.py": "import conftest\n"}, ["tests/support/y.py:1"]),
        case("a source module under src is no importer", {"tests/conftest.py": "", "src/p/m.py": "import conftest\n"}, []),
        case("a source module named test_helpers is no target", {"src/p/test_helpers.py": "", "tests/support/y.py": "import test_helpers\n"}, []),
        case("exact case", {"tests/conftest.py": "", "tests/support/y.py": "import Conftest\n"}, []),
    ],
)
def test_an_import_of_a_test_module_or_conftest_is_red(build_repo, files, expected):
    """TESTIMPORT-1, TESTIMPORT-2, TESTIMPORT-4, TESTIMPORT-5, TESTIMPORT-8, TESTIMPORT-9, TESTIMPORT-10, TESTIMPORT-11,
    TESTIMPORT-12: which names are red, which importers are judged and which paths are targets."""
    assert red(build_repo(project(files)), RULE) == expected


def test_a_members_test_importing_the_roots_conftest_is_red(build_repo):
    """LAYOUT-1, TESTIMPORT-7: a member's test importing the root's conftest is red."""
    repo = build_repo(project({
        "tests/conftest.py": "", "m/pyproject.toml": "", "m/tests/test_x.py": "from tests.conftest import fixture\n",
    }))
    assert red(repo, RULE) == ["m/tests/test_x.py:1"]


def test_a_test_module_and_a_conftest_are_importers_too(build_repo):
    """TESTIMPORT-2: a test module and a conftest are importers too."""
    repo = build_repo(project({
        "tests/conftest.py": "", "tests/test_a.py": "from tests.conftest import f\n", "tests/api/conftest.py": "import test_a\n",
    }))
    assert red(repo, RULE) == ["tests/api/conftest.py:1", "tests/test_a.py:1"]


def test_a_symlink_is_never_read_but_is_a_target_by_its_name(build_repo):
    """TESTIMPORT-3, TESTIMPORT-6: a link importing conftest is unread, and a link named test_x.py is imported."""
    repo = build_repo(project({"tests/conftest.py": "", "tests/real.py": "import conftest\n", "tests/support/y.py": "import test_x\n"}))
    (repo / "tests" / "helper.py").symlink_to("real.py")
    (repo / "tests" / "test_x.py").symlink_to("real.py")
    in_index(repo, "add", "--", "tests/helper.py", "tests/test_x.py")
    assert red(repo, RULE) == ["tests/real.py:1", "tests/support/y.py:1"]


def test_one_statement_is_one_finding_naming_each_target(build_repo, capsys):
    """TESTIMPORT-13, TESTIMPORT-14, ROW-7, ROW-14: one statement is one finding at `path:line` naming each target."""
    repo = build_repo(project({"tests/conftest.py": "", "tests/test_x.py": "", "tests/support/y.py": "import os, conftest, test_x\n"}))
    code = check_output(capsys, repo)[0]
    finding = assert_prints_as_kind(capsys, repo, RULE, FindingEvent.TEST_IMPORT, "conftest", "test_x")
    assert (code, finding.location) == (1, "tests/support/y.py:1")


def test_two_importing_lines_print_two_rows_and_one_fix_after_the_second(build_repo, capsys):
    """TESTIMPORT-13, TESTIMPORT-15, ROW-3: a fix shared by consecutive findings prints once; its entry clears both."""
    content = "import conftest\nfrom tests.test_x import a\n"
    repo = build_repo(project({"tests/conftest.py": "", "tests/test_x.py": "", "tests/support/y.py": content}))
    output = check_output(capsys, repo)[1]
    found = rule_findings(repo, RULE)
    rows = [row for finding in found for row in rows_of(output, finding)]
    assert ([row.fix_follows for row in rows], output.count(f" {FIX_LABEL} {found[0].fix}")) == ([False, True], 1)
    put(repo / CONFIG, ONE_JOB + declaring("tests/support/y.py", rule=RULE))
    assert red(repo, RULE) == []


def test_findings_are_all_printed_sorted_by_path_and_line(build_repo):
    """TESTIMPORT-20: findings are sorted by path and line, not in the order the walk finds the statements."""
    repo = build_repo(project({
        "tests/conftest.py": "",
        "tests/support/b.py": "def f():\n    import conftest\n\nimport conftest\n",
        "tests/support/a.py": "x = 1\ny = 2\nimport conftest\n",
    }))
    assert red(repo, RULE) == ["tests/support/a.py:3", "tests/support/b.py:2", "tests/support/b.py:4"]


@pytest.mark.parametrize(
    "content",
    ["def broken(:\n", "# coding: nonesuch\n", "x = 1\x00\n"],
    ids=["a syntax error", "an unknown coding cookie", "a null byte"],
)
def test_an_importer_python_cannot_parse_is_unparsable(build_repo, capsys, content):
    """TESTIMPORT-16, TESTIMPORT-17: an importer Python cannot decode or parse makes the files row not checked."""
    code, output = check_output(capsys, build_repo(project({"tests/support/y.py": content})))
    assert code == 2
    assert f"{refusal_row('files')}tests/support/y.py is not valid Python (" in output


def test_a_file_with_a_byte_order_mark_is_parsed_and_judged(build_repo):
    """TESTIMPORT-16: a file with a byte order mark is decoded as Python reads it and judged."""
    repo = build_repo(project({"tests/conftest.py": "", "tests/support/y.py": "\ufeffimport conftest\n"}))
    assert red(repo, RULE) == ["tests/support/y.py:1"]


def test_an_unreadable_importer_cannot_be_judged_but_other_files_are_not_read(build_repo, capsys):
    """TESTIMPORT-18, TESTIMPORT-19: an importer that cannot be read cannot be judged, and no other file is read."""
    repo = build_repo(project({"tests/support/y.py": "x = 1\n", "tests/fixtures/data.py": "x = 1\n", "docs/n.txt": "x\n"}))
    (repo / "tests" / "fixtures" / "data.py").chmod(0)
    (repo / "docs" / "n.txt").chmod(0)
    assert "test-import" not in refusals(repo)
    (repo / "tests" / "support" / "y.py").chmod(0)
    code, _ = check_output(capsys, repo)
    assert (code, "test-import" in refusals(repo)) == (2, True)


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        (ONE_JOB + declaring("tests/support/y.py", rule=RULE), []),
        (ONE_JOB + declaring("tests/support/y.py", rule="test-layout"), ["tests/support/y.py:1"]),
        (ONE_JOB + declaring("tests/gone.py", rule=RULE), ["tests/gone.py", "tests/support/y.py:1"]),
    ],
    ids=["declared", "declared under test-layout only", "declared but untracked"],
)
def test_a_declaration_exempts_only_its_importer_under_its_own_rule(build_repo, config, expected):
    """TESTIMPORT-15, SUFFIX-10, SUFFIX-12: a declaration covers its importer under its own rule; a stale one is red."""
    repo = build_repo(project({"tests/conftest.py": "", "tests/support/y.py": "import conftest\n", CONFIG: config}))
    assert red(repo, RULE) == expected
