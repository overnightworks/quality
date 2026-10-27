"""#199: the test-layout rule maps every test module to the module it tests."""

import pytest

from tests.support.emergency import ONE_JOB
from tests.support.hygiene import hygiene_output
from tests.support.layout import CONFIG, GUIDANCE, declaring, findings, in_index, red

RULE = "test-layout"


def project(*paths: str) -> dict[str, str]:
    """A root project (its `pyproject.toml` is added) with the given tracked paths, all empty."""
    return {**GUIDANCE, "pyproject.toml": "", **dict.fromkeys(paths, "")}


def case(identifier: str, paths: list[str], expected: list[str], *more: str):
    return pytest.param(paths, expected, *more, id=identifier)


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        case("B1 prefix at a word boundary", ["src/p/verdict.py", "tests/test_verdict_reread.py"], []),
        case("B1 no prefix across a word", ["src/p/verdict.py", "tests/test_verdicts.py"], ["tests/test_verdicts.py"]),
        case("B2 a longer word run without its first word", ["src/p/limiter.py", "tests/test_redis_rate_limiter.py"],
             ["tests/test_redis_rate_limiter.py"]),
        case("B3 exact case", ["src/p/verdict.py", "tests/test_Verdict.py"], ["tests/test_Verdict.py"]),
        case("B4 a package folder", ["src/p/config/x.py", "tests/test_config.py"], []),
        case("B5 one underscore dropped from a module", ["src/p/_x.py", "tests/test_x.py"], []),
        case("B5 one underscore dropped from a package", ["src/p/_x/y.py", "tests/test_x.py"], []),
        case("B5 the test keeps no underscore", ["src/p/_x.py", "src/p/_.py", "tests/test__x.py"], ["tests/test__x.py"]),
        case("B6 main", ["src/p/__main__.py", "tests/test_main.py"], []),
        case("B6 init", ["src/p/__init__.py", "tests/test_init.py", "tests/test_init_exports.py"], []),
        case("B6 about", ["src/p/__about__.py", "tests/test_about.py"], []),
        case("B7 an empty name", ["src/p/x.py", "src/p/_.py", "tests/test_.py"], ["tests/test_.py"]),
        case("B8 the package's own name", ["src/webauth/x.py", "tests/test_webauth_public_api.py"], []),
        case("B9 a mirrored folder", ["src/p/api/routes.py", "tests/api/test_routes.py"], []),
        case("B9 a module is no folder", ["src/p/hygiene.py", "tests/hygiene/test_x.py"], ["tests/hygiene/test_x.py"]),
        case("B10 a folder only above a package", ["src/p/api/v1/routes.py", "tests/test_api.py", "tests/api/v1/test_routes.py"], []),
        case("B10 a folder with no python", ["src/p/x.py", "src/p/static/logo.svg", "tests/test_static.py"],
             ["tests/test_static.py"]),
    ],
)
def test_a_test_is_named_after_a_module_its_folder_mirrors(build_repo, capsys, paths, expected):
    """#199 sentences 1, 3 and 4."""
    assert red(capsys, build_repo(project(*paths)), RULE) == expected


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        case("B8 the own name of one of several packages", ["src/a/x.py", "src/b/y.py", "tests/a/test_a_api.py"], []),
        case("B11 under its package", ["src/a/x.py", "src/b/y.py", "tests/a/test_x.py"], []),
        case("B11 not under a package", ["src/a/x.py", "src/b/y.py", "tests/test_x.py", "tests/other/test_x.py"],
             ["tests/other/test_x.py", "tests/test_x.py"]),
        case("B12 scripts come first", ["src/a/x.py", "src/b/y.py", "scripts/x.py", "tests/scripts/test_x.py"], []),
    ],
)
def test_with_several_packages_each_test_lies_under_its_package(build_repo, capsys, paths, expected):
    """#199 sentences 1 and 2."""
    assert red(capsys, build_repo(project(*paths)), RULE) == expected


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        case("B13 a script", ["scripts/verify_release.py", "tests/scripts/test_verify_release.py"], []),
        case("B13 no such script", ["tests/scripts/test_verify_release.py"], ["tests/scripts/test_verify_release.py"]),
        case("B13 a script in a folder", ["scripts/deploy/push.sh", "tests/scripts/deploy/test_push.py"], []),
        case("B15a no scripts folder", ["tests/scripts/deploy/test_x.py"], ["tests/scripts/deploy/test_x.py"]),
        case("B14 a workflow", [".github/workflows/gate.yml", "tests/workflows/test_gate_sonar.py"], []),
        case("B14 a hyphen reads as an underscore", [".github/workflows/code-scanning.yml",
                                                     "tests/workflows/test_code_scanning.py"], []),
        case("B14 a yaml suffix", [".github/workflows/x.yaml", "tests/workflows/test_x.py"], []),
        case("B15a no workflows folder", ["tests/workflows/test_x.py"], ["tests/workflows/test_x.py"]),
    ],
)
def test_script_and_workflow_tests_mirror_their_own_folders(build_repo, capsys, paths, expected):
    """#199 sentences 2 and 3."""
    assert red(capsys, build_repo(project("src/p/x.py", *paths)), RULE) == expected


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        case("B17 support", ["tests/support/test_x.py", "tests/api/support/test_x.py"],
             ["tests/api/support/test_x.py", "tests/support/test_x.py"]),
        case("B18 e2e and fixtures", ["tests/e2e/test_flow.py", "tests/fixtures/test_x.py"], []),
    ],
)
def test_support_is_judged_while_e2e_and_fixtures_are_not(build_repo, capsys, paths, expected):
    """#199 sentence 5."""
    assert red(capsys, build_repo(project("src/p/x.py", *paths)), RULE) == expected


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        case("B19 a member's own src", ["m/pyproject.toml", "m/src/q/x.py", "m/tests/test_x.py"], []),
        case("B19 not the root's module", ["src/p/y.py", "m/pyproject.toml", "m/src/q/x.py", "m/tests/test_y.py"],
             ["m/tests/test_y.py"]),
        case("B16 a workflow test in a member", ["m/pyproject.toml", "m/src/q/x.py", ".github/workflows/ci.yml",
                                                 "m/tests/workflows/test_ci.py"], ["m/tests/workflows/test_ci.py"]),
        case("B13 a member's own script", ["m/pyproject.toml", "m/src/q/y.py", "m/scripts/x.py", "m/tests/scripts/test_x.py"], []),
        case("B13 not the root's script", ["scripts/x.py", "m/pyproject.toml", "m/src/q/y.py", "m/tests/scripts/test_x.py"],
             ["m/tests/scripts/test_x.py"]),
        case("B20 nested members", ["speech/pyproject.toml", "speech/src/s/x.py", "speech/tests/test_x.py",
                                    "speech/providers/qwen/pyproject.toml", "speech/providers/qwen/src/q/y.py",
                                    "speech/providers/qwen/tests/test_y.py"], []),
    ],
)
def test_a_member_maps_only_into_its_own_project(build_repo, capsys, files, expected):
    """#199 sentence 7."""
    assert red(capsys, build_repo(project(*files)), RULE) == expected


def test_each_failing_case_has_its_own_reason_and_fix(build_repo, capsys):
    """#199 sentence 6 and E1: every case prints, sorted by path, and the folder failure names its target."""
    repo = build_repo(project(
        "src/p/api/v1/routes.py", "tests/support/test_a.py", "tests/hygiene/test_b.py", "tests/test_c.py",
        "tests/workflows/test_d.py",
    ))
    code, output = hygiene_output(capsys, repo)
    assert (code, findings(output, RULE)) == (1, [
        "tests/hygiene/test_b.py", "tests/support/test_a.py", "tests/test_c.py", "tests/workflows/test_d.py",
    ])
    for words in (
        "a test module in a support/ folder, which holds helpers",
        "rename it if it is a helper, or move it to the folder that mirrors its module",
        "its folder mirrors src/p/hygiene, which is no package folder",
        "move it to the folder that mirrors its module's package",
        "no module or package in src/p is named by a word prefix of c",
        "rename it test_<module>[_<aspect>].py after what it tests",
        "no workflow in .github/workflows is named by a word prefix of d",
        "or declare it in quality/config.toml with [[layout.allow]]",
    ):
        assert words in output


def test_a_project_with_several_packages_names_the_placement(build_repo, capsys):
    """#199 sentence 6: the placement case."""
    output = hygiene_output(capsys, build_repo(project("src/a/x.py", "src/b/y.py", "tests/test_x.py")))[1]
    assert "a project with several packages keeps each test under tests/<package>/" in output
    assert "move it under tests/<package>/ of the package it tests" in output


@pytest.mark.parametrize(
    ("paths", "reasons", "fix"),
    [
        case("B15 two reasons", ["tests/workflows/ci/test_lint.py"],
             ["workflow tests lie directly in tests/workflows/",
              "no workflow in .github/workflows is named by a word prefix of lint"],
             "move it up into tests/workflows/, or rename it test_<workflow>[_<aspect>].py after what it tests, "
             "or declare it in quality/config.toml with [[layout.allow]], its path, the rule test-layout and a reason."),
        case("B15 one reason", ["tests/workflows/ci/test_lint.py", ".github/workflows/lint.yml"],
             ["workflow tests lie directly in tests/workflows/"],
             "move it up into tests/workflows/, or declare it in quality/config.toml with [[layout.allow]], its path, the rule test-layout and a reason."),
        case("B16 the member case", ["m/pyproject.toml", "m/src/q/x.py", "m/tests/workflows/test_lint.py"],
             ["workflow tests belong to the root project's tests/workflows/"],
             "move it to the root's tests/workflows/, or declare it in quality/config.toml with [[layout.allow]], its path, the rule test-layout and a reason."),
    ],
)
def test_workflow_tests_lie_directly_in_the_roots_workflows_folder(build_repo, capsys, paths, reasons, fix):
    """#199 sentences 2 and 6: two failures on one file give one finding, joined by a semicolon."""
    output = hygiene_output(capsys, build_repo(project("src/p/x.py", *paths)))[1]
    line = next(line for line in output.splitlines() if line.startswith(f"{RULE} "))
    assert "; ".join(reasons) in line
    assert f"  Fix: {fix}" in output.splitlines()


def test_a_declared_test_module_is_not_judged(build_repo, capsys):
    """#199 sentence 8, B22."""
    repo = build_repo({**project("src/p/x.py", "tests/test_y.py"), CONFIG: ONE_JOB + declaring("tests/test_y.py", rule=RULE)})
    assert red(capsys, repo, RULE) == []


def test_a_link_counts_by_its_name_and_is_never_followed(build_repo, capsys):
    """#199 sentence 8, B21: a linked test is judged by its name, a linked module maps a test."""
    repo = build_repo(project("src/p/x.py", "tests/support/helper.py", "tests/test_y.py"))
    (repo / "tests" / "test_z.py").symlink_to("support/helper.py")
    (repo / "src" / "p" / "y.py").symlink_to("x.py")
    in_index(repo, "add", "--", "tests/test_z.py", "src/p/y.py")
    assert red(capsys, repo, RULE) == ["tests/test_z.py"]


def test_an_empty_test_name_says_it_is_empty(build_repo, capsys):
    """#201 folded follow-up: the reason never reads "a word prefix of" an empty aspect."""
    output = hygiene_output(capsys, build_repo(project("src/p/x.py", "tests/test_.py")))[1]
    assert "is named by an empty name" in output
