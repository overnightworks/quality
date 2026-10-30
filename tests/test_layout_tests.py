"""#196: the test-layout rule, what may lie under a Python project's `tests/`."""

import pytest

from tests.support.check import hygiene_output, refusals
from tests.support.layout import CONFIG, GUIDANCE, declaring, findings, in_index, red
from tests.support.merge import ONE_JOB

RULE = "test-layout"
ROOT_PROJECT = {**GUIDANCE, "pyproject.toml": "", "src/p/cli.py": "x = 1\n"}


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ({"tests/test_cli.py": "", "tests/conftest.py": "", "tests/support/x.py": ""}, []),
        ({"tests/advisory_support.py": "", "tests/support/advisories.py": ""}, ["tests/advisory_support.py"]),
        ({"tests/data/x.yml": "", "tests/fixtures/x.yml": "", "tests/a/fixtures/x.json": ""}, ["tests/data/x.yml"]),
        ({"tests/e2e/flow/helper.py": "", "tests/e2e/data.json": "", "tests/x/e2e/helper.py": ""}, ["tests/x/e2e/helper.py"]),
        ({"tests/conftest.py": "", "tests/api/conftest.py": ""}, []),
        ({"tests/foo.test.mjs": "", "tests/check.sh": ""}, ["tests/check.sh", "tests/foo.test.mjs"]),
        ({"tests/test_cli.py": "", "tests/fixtures/test_x.py": ""}, []),
        ({"tests/Support/x.py": ""}, ["tests/Support/x.py"]),
    ],
    ids=["clean", "helper beside tests", "data folder", "e2e only at the top", "conftest at any depth",
         "other languages", "a test file", "exact case"],
)
def test_rule_four_judges_every_path_under_tests(build_repo, capsys, files, expected):
    """#196 sentences 3 and 5: a test file, a conftest, support/fixtures at any depth and top-level e2e pass."""
    assert red(capsys, build_repo({**ROOT_PROJECT, **files}), RULE) == expected


@pytest.mark.parametrize(
    ("init_files", "expected"),
    [
        (["tests/__init__.py", "tests/support/__init__.py", "tests/e2e/__init__.py", "tests/e2e/support/__init__.py"],
         ["tests/__init__.py", "tests/e2e/__init__.py", "tests/e2e/support/__init__.py", "tests/support/__init__.py"]),
        (["tests/fixtures/pkg/__init__.py", "tests/support/fixtures/__init__.py"], []),
    ],
    ids=["red anywhere", "data inside fixtures"],
)
def test_an_init_file_under_tests_is_red_unless_it_is_fixture_data(build_repo, capsys, init_files, expected):
    """#196 sentence 4."""
    output = hygiene_output(capsys, build_repo({**ROOT_PROJECT, **dict.fromkeys(init_files, "")}))[1]
    assert findings(output, RULE) == expected
    assert ("delete it, or declare it" in output) == bool(expected)


def test_each_finding_case_has_its_own_reason_and_fix(build_repo, capsys):
    """#196 sentence 6: both ways out are named; #196 E1: all printed, sorted by path."""
    repo = build_repo({**ROOT_PROJECT, "tests/__init__.py": "", "tests/helpers.py": ""})
    code, output = hygiene_output(capsys, repo)
    first, second = output.split(f"{RULE} tests/helpers.py")
    assert code == 1
    assert "pytest's importlib mode does not need" in first
    assert "delete it, or declare it" in first
    assert "a support/ folder (a helper) or a fixtures/ folder (data)" in second
    assert "the rule test-layout" in second


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ({"pyproject.toml": "", "tests/helpers.py": ""}, []),
        ({"tests/helpers.py": "", "src/p/x.py": ""}, []),
        ({"pyproject.toml": "", "src/static/logo.svg": "", "tests/helpers.py": ""}, []),
        ({"pyproject.toml": "", "src/p/x.py": "", "tests/helpers.py": ""}, ["tests/helpers.py"]),
        ({"pyproject.toml": "[tool.x]\n", "src/p/deep/x.py": "", "tests/helpers.py": ""}, ["tests/helpers.py"]),
        ({"m/pyproject.toml": "", "m/src/p/x.py": "", "m/tests/helpers.py": ""}, ["m/tests/helpers.py"]),
        ({"pyproject.toml": "", "src/p/x.py": "", "tools/tests/helper.py": ""}, []),
        ({"pyproject.toml": "", "src/p/x.py": "", "tests/fixtures/app/pyproject.toml": "",
          "tests/fixtures/app/src/a/x.py": "", "tests/fixtures/app/tests/helpers.py": ""}, []),
        ({"pyproject.toml": "", "tests/pyproject.toml": "", "tests/src/a/x.py": "", "tests/tests/helpers.py": ""},
         []),
        ({"pyproject.toml": "", "src/modules.py": "", "tests/helpers.py": ""}, []),
    ],
    ids=["no src", "no pyproject", "src without python", "namespace package", "empty project table", "member",
         "tests elsewhere", "pyproject inside tests makes no project", "pyproject directly in tests", "src holding only a module"],
)
def test_only_a_project_with_a_package_is_judged(build_repo, capsys, files, expected):
    """#196 sentences 1 and 2, cells A2 to A10."""
    assert red(capsys, build_repo({**GUIDANCE, **files}), RULE) == expected


def test_nested_projects_each_judge_their_own_tests(build_repo, capsys):
    """#196 sentence 1, A8: `speech/tests` belongs to speech, `speech/providers/qwen/tests` to qwen."""
    files = {
        "speech/pyproject.toml": "", "speech/src/s/x.py": "", "speech/tests/support/x.py": "",
        "speech/providers/qwen/pyproject.toml": "", "speech/providers/qwen/src/q/x.py": "",
        "speech/providers/qwen/tests/helpers.py": "",
    }
    assert red(capsys, build_repo({**GUIDANCE, **files}), RULE) == ["speech/providers/qwen/tests/helpers.py"]


def test_a_symlink_and_a_gitlink_under_tests_are_judged_by_their_own_name(build_repo, capsys):
    """#196 sentence 8, D1 and D2: never followed."""
    repo = build_repo({**ROOT_PROJECT, "tests/support/x.py": ""})
    (repo / "tests" / "helper.py").symlink_to("support/x.py")
    in_index(repo, "add", "--", "tests/helper.py")
    in_index(repo, "update-index", "--add", "--cacheinfo", f"160000,{'1' * 40},tests/vendor")
    assert red(capsys, repo, RULE) == ["tests/helper.py", "tests/vendor"]


@pytest.mark.parametrize(
    ("tracked", "declarations", "expected"),
    [
        (["tests/helpers.py"], [("tests/helpers.py", RULE)], []),
        (["tests/helpers.py"], [("tests/helpers.py", "file-suffix")], ["tests/helpers.py"]),
        ([], [("tests/gone.py", RULE)], ["tests/gone.py"]),
        (["tests/test_cli.py"], [("tests/test_cli.py", RULE)], []),
    ],
    ids=["declared", "declared under another rule", "not tracked", "already complying"],
)
def test_a_declaration_covers_only_its_own_rule_and_a_stale_one_is_a_finding(build_repo, capsys, tracked, declarations, expected):
    """#196 sentence 7, D3 to D5."""
    config = ONE_JOB + "".join(declaring(path, rule=rule) for path, rule in declarations)
    repo = build_repo({**ROOT_PROJECT, **dict.fromkeys(tracked, ""), CONFIG: config})
    assert red(capsys, repo, RULE) == expected


def test_a_malformed_layout_table_cannot_be_judged_for_test_layout(build_repo, capsys):
    """#196 sentence 7, D6."""
    duplicate = declaring("tests/helpers.py", rule=RULE) * 2
    repo = build_repo({**ROOT_PROJECT, "tests/helpers.py": "", CONFIG: ONE_JOB + duplicate})
    code, _ = hygiene_output(capsys, repo)
    assert (code, RULE in refusals(repo)) == (2, True)


def test_an_index_record_that_cannot_be_read_cannot_be_judged(build_repo, capsys, fake_git):
    """#196 sentence 8, D7."""
    repo = build_repo(GUIDANCE)
    fake_git(r"""case " $* " in *" -s "*) printf 'not-a-mode abc 0\tAGENTS.md\0'; exit 0;; esac""")
    code, _ = hygiene_output(capsys, repo)
    assert (code, RULE in refusals(repo)) == (2, True)
