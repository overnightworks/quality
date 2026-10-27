"""#201: the test-import rule, no file under a Python project's `tests/` imports a test module or a conftest."""

import pytest

from tests.support.emergency import ONE_JOB
from tests.support.hygiene import hygiene_output
from tests.support.layout import CONFIG, GUIDANCE, case, declaring, findings, in_index, red

RULE = "test-import"


def project(files: dict[str, str]) -> dict[str, str]:
    """A root project (its `pyproject.toml` is added) tracking `files`."""
    return {**GUIDANCE, "pyproject.toml": "", **files}


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        case("G1 import conftest", {"tests/conftest.py": "", "tests/support/x.py": "import conftest\n"}, ["tests/support/x.py:1"]),
        case("G1 from conftest", {"tests/conftest.py": "", "tests/support/x.py": "from conftest import f\n"}, ["tests/support/x.py:1"]),
        case("G2 dotted test module", {"tests/test_a.py": "", "tests/support/x.py": "from tests.test_a import y\n"}, ["tests/support/x.py:1"]),
        case("G2 dotted e2e conftest", {"tests/e2e/conftest.py": "", "tests/support/x.py": "import tests.e2e.conftest\n"}, ["tests/support/x.py:1"]),
        case("G3 a from-import of a conftest by name", {"tests/conftest.py": "", "tests/support/x.py": "from tests import conftest\n"}, ["tests/support/x.py:1"]),
        case("G3 a name that is no module", {"tests/test_a.py": "", "tests/support/x.py": "from tests.support.x import test_helper\n"}, []),
        case("G4 relative test module", {"tests/test_a.py": "", "tests/support/x.py": "from ..test_a import y\n"}, ["tests/support/x.py:1"]),
        case("G4 relative conftest by name", {"tests/conftest.py": "", "tests/x.py": "from . import conftest\n"}, ["tests/x.py:1"]),
        case("G4 relative conftest above", {"tests/conftest.py": "", "tests/support/x.py": "from ..conftest import z\n"}, ["tests/support/x.py:1"]),
        case("G4 relative support module", {"tests/support/y.py": "", "tests/support/x.py": "from .y import z\n"}, []),
        case("G4 a relative name matches only the exact path", {"tests/test_a.py": "", "tests/support/x.py": "from .test_a import y\n"}, []),
        case("G5 bare test module", {"tests/test_x.py": "", "tests/support/y.py": "import test_x\n"}, ["tests/support/y.py:1"]),
        case("G5 a third-party name", {"tests/test_x.py": "", "tests/support/y.py": "import test_utils\n"}, []),
        case("G6 a pythonpath spelling", {"tests/test_rule_option.py": "", "tests/support/y.py": "import test_rule_option\n"}, ["tests/support/y.py:1"]),
        case("G6 an e2e-rooted spelling", {"tests/e2e/conftest.py": "", "tests/support/y.py": "from e2e.conftest import x\n"}, ["tests/support/y.py:1"]),
        case("G6 a suffix aligns by component", {"tests/conftest.py": "", "tests/support/y.py": "import myconftest\n"}, []),
        case("G9 an e2e support module", {"tests/e2e/support/x.py": "", "tests/e2e/prove.py": "import tests.e2e.support.x\n"}, []),
        case("G9 a sibling driver", {"tests/e2e/oidc/prove.py": "", "tests/e2e/oidc/run.py": "import prove\nfrom . import prove\n"}, []),
        case("G9 an e2e driver importing a conftest", {"tests/conftest.py": "", "tests/e2e/prove.py": "import conftest\n"}, ["tests/e2e/prove.py:1"]),
        case("G9 an e2e conftest importing a test module", {"tests/test_a.py": "", "tests/e2e/conftest.py": "import test_a\n"}, ["tests/e2e/conftest.py:1"]),
        case("G10 inside a function", {"tests/conftest.py": "", "tests/support/y.py": "def f():\n    import conftest\n"}, ["tests/support/y.py:2"]),
        case("G10 under TYPE_CHECKING",
             {"tests/conftest.py": "", "tests/support/y.py": "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import conftest\n"},
             ["tests/support/y.py:3"]),
        case("G12 a fixture file is not judged", {"tests/conftest.py": "", "tests/fixtures/s/m.py": "import conftest\n"}, []),
        case("G12 a fixture file is no target", {"tests/fixtures/s/test_a.py": "", "tests/support/y.py": "import test_a\n"}, []),
        case("G14 a project without src", {"tests/conftest.py": "", "tests/support/y.py": "import conftest\n"}, ["tests/support/y.py:1"]),
        case("a source module under src is no importer", {"tests/conftest.py": "", "src/p/m.py": "import conftest\n"}, []),
        case("a source module named test_helpers is no target", {"src/p/test_helpers.py": "", "tests/support/y.py": "import test_helpers\n"}, []),
        case("exact case", {"tests/conftest.py": "", "tests/support/y.py": "import Conftest\n"}, []),
    ],
)
def test_an_import_of_a_test_module_or_conftest_is_red(build_repo, capsys, files, expected):
    """#201 sentences 1 to 5: which names are red, which importers are judged, which paths are targets."""
    assert red(capsys, build_repo(project(files)), RULE) == expected


def test_a_members_test_importing_the_roots_conftest_is_red(build_repo, capsys):
    """#201 G7, sentence 2."""
    repo = build_repo(project({
        "tests/conftest.py": "", "m/pyproject.toml": "", "m/tests/test_x.py": "from tests.conftest import fixture\n",
    }))
    assert red(capsys, repo, RULE) == ["m/tests/test_x.py:1"]


def test_a_test_module_and_a_conftest_are_importers_too(build_repo, capsys):
    """#201 G8, sentence 1."""
    repo = build_repo(project({
        "tests/conftest.py": "", "tests/test_a.py": "from tests.conftest import f\n", "tests/api/conftest.py": "import test_a\n",
    }))
    assert red(capsys, repo, RULE) == ["tests/api/conftest.py:1", "tests/test_a.py:1"]


def test_a_symlink_is_never_read_but_is_a_target_by_its_name(build_repo, capsys):
    """#201 G13, sentences 1 and 2: a link importing conftest is unread, and a link named test_x.py is imported."""
    repo = build_repo(project({"tests/conftest.py": "", "tests/real.py": "import conftest\n", "tests/support/y.py": "import test_x\n"}))
    (repo / "tests" / "helper.py").symlink_to("real.py")
    (repo / "tests" / "test_x.py").symlink_to("real.py")
    in_index(repo, "add", "--", "tests/helper.py", "tests/test_x.py")
    assert red(capsys, repo, RULE) == ["tests/real.py:1", "tests/support/y.py:1"]


def test_one_statement_is_one_finding_naming_each_target(build_repo, capsys):
    """#201 G11, sentence 6."""
    repo = build_repo(project({"tests/conftest.py": "", "tests/test_x.py": "", "tests/support/y.py": "import os, conftest, test_x\n"}))
    code, output = hygiene_output(capsys, repo)
    assert (code, findings(output, RULE)) == (1, ["tests/support/y.py:1"])
    assert "imports the test module or conftest conftest, test_x" in output
    assert "move what it needs to a support/ module, or make it a fixture in conftest.py" in output
    assert "the rule test-import" in output


def test_findings_are_all_printed_sorted_by_path_and_line(build_repo, capsys):
    """#201 G19, sentence 6: path before line, and a later top-level import precedes, in the walk, an earlier one nested in a function."""
    repo = build_repo(project({
        "tests/conftest.py": "",
        "tests/support/b.py": "def f():\n    import conftest\n\nimport conftest\n",
        "tests/support/a.py": "x = 1\ny = 2\nimport conftest\n",
    }))
    assert red(capsys, repo, RULE) == ["tests/support/a.py:3", "tests/support/b.py:2", "tests/support/b.py:4"]


@pytest.mark.parametrize(
    "content",
    ["def broken(:\n", "# coding: nonesuch\n", "x = 1\x00\n"],
    ids=["a syntax error", "an unknown coding cookie", "a null byte"],
)
def test_an_importer_python_cannot_parse_is_unparsable(build_repo, capsys, content):
    """#201 G15, sentence 7."""
    code, output = hygiene_output(capsys, build_repo(project({"tests/support/y.py": content})))
    assert code == 2
    assert "hygiene: test-import unparsable - tests/support/y.py is not valid Python (" in output


def test_a_file_with_a_byte_order_mark_is_parsed_and_judged(build_repo, capsys):
    """#201 G16, sentence 7."""
    repo = build_repo(project({"tests/conftest.py": "", "tests/support/y.py": "\ufeffimport conftest\n"}))
    assert red(capsys, repo, RULE) == ["tests/support/y.py:1"]


def test_an_unreadable_importer_cannot_be_judged_but_other_files_are_not_read(build_repo, capsys):
    """#201 G17, sentence 7: only an importer is read."""
    repo = build_repo(project({"tests/support/y.py": "x = 1\n", "tests/fixtures/data.py": "x = 1\n", "docs/n.txt": "x\n"}))
    (repo / "tests" / "fixtures" / "data.py").chmod(0)
    (repo / "docs" / "n.txt").chmod(0)
    assert "hygiene: test-import cannot judge" not in hygiene_output(capsys, repo)[1]
    (repo / "tests" / "support" / "y.py").chmod(0)
    code, output = hygiene_output(capsys, repo)
    assert (code, "hygiene: test-import cannot judge - " in output) == (2, True)


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        (ONE_JOB + declaring("tests/support/y.py", rule=RULE), []),
        (ONE_JOB + declaring("tests/support/y.py", rule="test-layout"), ["tests/support/y.py:1"]),
        (ONE_JOB + declaring("tests/gone.py", rule=RULE), ["tests/gone.py", "tests/support/y.py:1"]),
    ],
    ids=["declared", "declared under test-layout only", "declared but untracked"],
)
def test_a_declaration_exempts_only_its_importer_under_its_own_rule(build_repo, capsys, config, expected):
    """#201 G18, sentence 8."""
    repo = build_repo(project({"tests/conftest.py": "", "tests/support/y.py": "import conftest\n", CONFIG: config}))
    assert red(capsys, repo, RULE) == expected
