"""#199: the test-layout rule maps every test module to the module it tests."""

import pytest

from quality import checkout, layout
from quality.report import FindingEvent
from quality.rules import Rule
from tests.support.check import hygiene_output, rule_findings
from tests.support.layout import (
    CONFIG,
    GUIDANCE,
    assert_empty_name_has_its_own_fact,
    assert_prints_as_kind,
    assert_row_is_kind,
    declaring,
    in_index,
    red,
)
from tests.support.merge import ONE_JOB

RULE = Rule.TEST_LAYOUT


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
    assert red(build_repo(project(*paths)), RULE) == expected


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
    assert red(build_repo(project(*paths)), RULE) == expected


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
    assert red(build_repo(project("src/p/x.py", *paths)), RULE) == expected


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
    assert red(build_repo(project("src/p/x.py", *paths)), RULE) == expected


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
    assert red(build_repo(project(*files)), RULE) == expected


def test_each_failing_case_is_a_misplaced_finding_naming_its_own_values(build_repo, capsys):
    """#199 sentence 6 and E1, #283 O12: every case prints, sorted by path, and the fact names the folder or name at fault."""
    repo = build_repo(project(
        "src/p/api/v1/routes.py", "tests/support/test_a.py", "tests/hygiene/test_b.py", "tests/test_c.py",
        "tests/workflows/test_d.py",
    ))
    code, output = hygiene_output(capsys, repo)
    found = {finding.path: finding for finding in rule_findings(repo, RULE)}
    assert (code, red(repo, RULE)) == (1, [
        "tests/hygiene/test_b.py", "tests/support/test_a.py", "tests/test_c.py", "tests/workflows/test_d.py",
    ])
    named = {
        "tests/hygiene/test_b.py": "src/p/hygiene",
        "tests/support/test_a.py": f"{layout.SUPPORT_DIRECTORY}/",
        "tests/test_c.py": "src/p",
        "tests/workflows/test_d.py": str(checkout.WORKFLOW_DIRECTORY),
    }
    for path, value in named.items():
        assert_row_is_kind(output, found[path], FindingEvent.MISPLACED, value)


def test_a_project_with_several_packages_is_a_misplaced_finding(build_repo, capsys):
    """#199 sentence 6: the placement case."""
    assert_prints_as_kind(capsys, build_repo(project("src/a/x.py", "src/b/y.py", "tests/test_x.py")), RULE, FindingEvent.MISPLACED)


WORKFLOW_TEST = "tests/workflows/ci/test_lint.py"


def test_two_failures_on_one_file_give_one_finding_joining_the_single_failures(build_repo, capsys):
    """#199 sentences 2 and 6, B15: the two-failure fact starts with the one-failure fact of the same folder problem, the
    fixes join by `, or ` (and the two-failure fix does not contain the one-failure fix), each naming the allowance once."""
    both = build_repo(project("src/p/x.py", WORKFLOW_TEST))
    (two,) = rule_findings(both, RULE)
    assert_prints_as_kind(capsys, both, RULE, FindingEvent.MISPLACED)
    (both / ".github" / "workflows").mkdir(parents=True)
    (both / ".github" / "workflows" / "lint.yml").write_text("name: lint\n")
    in_index(both, "add", "--", ".github/workflows/lint.yml")
    (one,) = rule_findings(both, RULE)
    assert two.reason.startswith(f"{one.reason}; ")
    assert (two.fix.count(RULE), one.fix.count(RULE), one.fix in two.fix) == (1, 1, False)


def test_a_workflow_test_in_a_member_is_a_misplaced_finding(build_repo, capsys):
    """#199 sentences 2 and 6, B16."""
    repo = build_repo(project("src/p/x.py", "m/pyproject.toml", "m/src/q/x.py", "m/tests/workflows/test_lint.py"))
    assert_prints_as_kind(capsys, repo, RULE, FindingEvent.MISPLACED)


def test_a_declared_test_module_is_not_judged(build_repo, capsys):
    """#199 sentence 8, B22."""
    repo = build_repo({**project("src/p/x.py", "tests/test_y.py"), CONFIG: ONE_JOB + declaring("tests/test_y.py", rule=RULE)})
    assert red(repo, RULE) == []


def test_a_link_counts_by_its_name_and_is_never_followed(build_repo, capsys):
    """#199 sentence 8, B21: a linked test is judged by its name, a linked module maps a test."""
    repo = build_repo(project("src/p/x.py", "tests/support/helper.py", "tests/test_y.py"))
    (repo / "tests" / "test_z.py").symlink_to("support/helper.py")
    (repo / "src" / "p" / "y.py").symlink_to("x.py")
    in_index(repo, "add", "--", "tests/test_z.py", "src/p/y.py")
    assert red(repo, RULE) == ["tests/test_z.py"]


def test_an_empty_test_name_is_not_described_as_a_name_with_nothing_in_it(build_repo, capsys):
    """#201 folded follow-up 1 (#199 sentence 6)."""
    repo = build_repo(project("src/p/x.py", "tests/test_.py", "tests/test_zz.py"))
    assert_empty_name_has_its_own_fact(capsys, repo, RULE, "tests/test_.py", "tests/test_zz.py", "zz")
