"""The test-layout rule maps every frontend test to the file it tests and keeps Playwright specs in `e2e/`."""

import re

import pytest

from quality import layout
from quality.report import FindingEvent
from quality.rules import Rule
from tests.support.check import check_output, rows_of, rule_findings
from tests.support.layout import (
    CONFIG,
    GUIDANCE,
    assert_empty_name_has_its_own_fact,
    assert_prints_as_kind,
    assert_row_is_kind,
    case,
    declaring,
    empty_files,
    in_index,
    red,
)
from tests.support.merge import ONE_JOB

RULE = Rule.TEST_LAYOUT
PACKAGE_JSON = {"package.json": "{}\n"}
PYTHON_PROJECT = {"pyproject.toml": "", "src/p/x.py": ""}


def frontend(*paths: str, manifest: str = "package.json") -> dict[str, str]:
    """A repository tracking `manifest` (default: a root `package.json`) and the given paths, all empty."""
    return {**GUIDANCE, manifest: "{}\n", **empty_files(*paths)}


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        case("a root project", frontend("src/a.ts", "src/a.test.ts"), []),
        case("a member project",
             frontend("frontend/package.json", "frontend/x.ts", "frontend/x.test.ts", "frontend/y.test.ts"),
             ["frontend/y.test.ts"]),
        case("no package.json", {**GUIDANCE, **empty_files("tests/x.test.mjs", "src/y.test.ts")}, []),
        case("a package.json in the fixtures of a Python project",
             {**GUIDANCE, **PYTHON_PROJECT, **empty_files("tests/fixtures/app/package.json", "tests/fixtures/app/z.test.ts")}, []),
        case("a pyproject.toml in the tests of a frontend project",
             frontend("frontend/package.json", "frontend/tests/fixtures/pyproject.toml", "frontend/tests/fixtures/src/p/x.py",
                      "frontend/tests/fixtures/tests/stray.txt"), []),
        case("a spec in a member's e2e",
             frontend("packages/ui/package.json", "packages/ui/e2e/f.spec.ts", "e2e/f.spec.ts"), []),
        case("a spec outside a member's e2e", frontend("packages/ui/package.json", "packages/ui/src/f.spec.ts"),
             ["packages/ui/src/f.spec.ts"]),
        case("the aspects of a test",
             frontend("src/a.svelte.ts", "src/a.svelte.test.ts", "src/App.svelte", "src/App.test.ts", "src/x.ts",
                      "src/x.browser.test.ts", "src/x.test-d.ts", "src/x.spec-d.ts", "src/m.mjs", "src/m.test.mjs"), []),
        case("a route test beside its page", frontend("src/page.test.ts", "src/+page.svelte"), []),
        case("a route test beside only a page server", frontend("src/page.test.ts", "src/+page.server.ts"), []),
        case("the other route files",
             frontend("src/layout.test.ts", "src/+layout.svelte", "src/error.test.ts", "src/+error.svelte",
                      "src/server.test.ts", "src/+server.ts", "src/hooks.server.test.ts", "src/hooks.server.ts"), []),
        case("a route test alone", frontend("src/page.test.ts"), ["src/page.test.ts"]),
        case("a prefix at a word boundary",
             frontend("src/AlbumCoverEditor.svelte", "src/AlbumCoverEditorLeaving.test.ts", "src/AlbumCoverEditor.leaving.test.ts",
                      "src/editor.ts", "src/editorUnloadGuard.test.ts", "src/detail.ts", "src/detail-back.test.ts",
                      "src/share.ts", "src/share_import.test.ts"), []),
        case("no sibling for a prefix", frontend("src/detail-back.test.ts"), ["src/detail-back.test.ts"]),
        case("a prefix that is no key", frontend("src/optionCard.ts", "src/optionDecision.test.ts"),
             ["src/optionDecision.test.ts"]),
        case("no word boundary", frontend("src/editor.ts", "src/editorx.test.ts"), ["src/editorx.test.ts"]),
        case("another case", frontend("src/editor.ts", "src/Editor.test.ts"), ["src/Editor.test.ts"]),
        case("an empty base", frontend("src/editor.ts", "src/.test.ts"), ["src/.test.ts"]),
        case("a leading underscore stays in the key", frontend("src/_helpers.ts", "src/_helpers.test.ts"), []),
        case("a type test is no subject", frontend("src/a.test.ts", "src/a.test-d.ts"),
             ["src/a.test-d.ts", "src/a.test.ts"]),
        case("a test component is no subject", frontend("src/Card.test.ts", "src/Card.test.svelte"),
             ["src/Card.test.ts"]),
        case("a folder is no subject", frontend("src/a.test.ts", "src/a/b.ts"), ["src/a.test.ts"]),
        case("data is a subject", frontend("src/schema.test.ts", "src/schema.json"), []),
        case("specs at any depth of e2e", frontend("e2e/f.spec.ts", "e2e/sub/f.spec.ts"), []),
        case("specs elsewhere", frontend("src/x.spec.ts", "tests/e2e/x.spec.ts", "src/a.spec.mjs"),
             ["src/a.spec.mjs", "src/x.spec.ts", "tests/e2e/x.spec.ts"]),
        case("a test in e2e beside its subject", frontend("e2e/helpers.ts", "e2e/helpers.test.ts"), []),
        case("a test in e2e alone", frontend("e2e/flow.test.ts"), ["e2e/flow.test.ts"]),
        case("files no rule judges",
             frontend("e2e/helpers.ts", "src/lib/test-utils/x.ts", "src/tests/setup.ts", "src/__tests__/x.ts", "src/Card.test.svelte"), []),
        case("one owner under a judged Python tests",
             {**frontend("tests/foo.test.ts", "tests/e2e/flow.spec.ts", "tests/support/x.test.ts", "tests/fixtures/a.test.ts",
                         "e2e/flow.spec.ts"), **PYTHON_PROJECT}, ["tests/foo.test.ts"]),
        case("a Python project without a package leaves its tests to the frontend",
             frontend("tests/foo.test.ts", manifest="pyproject.toml") | PACKAGE_JSON, ["tests/foo.test.ts"]),
        case("a Cargo tree gives no finding", frontend("crates/a/Cargo.toml", "crates/a/tests/y.rs"), []),
        case("a package.json in a Cargo tree is a project",
             {**GUIDANCE, **empty_files("Cargo.toml", "tools/package.json", "tools/a.test.mjs")}, ["tools/a.test.mjs"]),
    ],
)
def test_the_sibling_and_the_e2e_folder_are_judged_per_frontend_project(build_repo, files, expected):
    """LAYOUT-2, LAYOUT-5, LAYOUT-32, LAYOUT-33, LAYOUT-34, LAYOUT-35, LAYOUT-36, LAYOUT-38, LAYOUT-39, LAYOUT-40,
    LAYOUT-41, LAYOUT-42, LAYOUT-43, LAYOUT-44, LAYOUT-45, LAYOUT-46: only a judged test file in a frontend project is
    red, once, by its name."""
    assert red(build_repo(files), RULE) == expected


def test_a_symlink_is_judged_by_its_name_and_a_gitlink_is_never_judged_or_a_subject(build_repo):
    """LAYOUT-37, LAYOUT-42: a symlinked test is judged by its name, a gitlinked test is never judged and a gitlink is
    no subject."""
    repo = build_repo(frontend("src/real.ts", "src/b.test.ts", "src/c.test.ts", "src/d.test.ts"))
    (repo / "src" / "a.ts").symlink_to("real.ts")
    (repo / "src" / "x.test.ts").symlink_to("real.ts")
    in_index(repo, "add", "--", "src/a.ts", "src/x.test.ts")
    in_index(repo, "update-index", "--add", "--cacheinfo", f"160000,{'1' * 40},src/b")
    in_index(repo, "update-index", "--add", "--cacheinfo", f"160000,{'1' * 40},src/y.test.ts")
    assert red(repo, RULE) == ["src/b.test.ts", "src/c.test.ts", "src/d.test.ts", "src/x.test.ts"]


def test_a_symlinked_subject_maps_a_test(build_repo):
    """LAYOUT-42: `a.test.ts` beside a symlink `a.ts` is clean."""
    repo = build_repo(frontend("src/real.ts", "src/a.test.ts"))
    (repo / "src" / "a.ts").symlink_to("real.ts")
    in_index(repo, "add", "--", "src/a.ts")
    assert red(repo, RULE) == []


@pytest.mark.parametrize(
    ("tracked", "declarations", "expected"),
    [
        (["src/x.test.ts"], [("src/x.test.ts", RULE)], []),
        (["src/x.spec.ts"], [("src/x.spec.ts", RULE)], []),
        (["src/x.test.ts"], [("src/x.test.ts", "file-suffix")], ["src/x.test.ts"]),
        ([], [("src/gone.test.ts", RULE)], ["src/gone.test.ts"]),
    ],
    ids=["a declared test", "a declared spec", "declared under another rule", "a stale declaration"],
)
def test_a_declaration_exempts_a_frontend_test_from_every_case(build_repo, tracked, declarations, expected):
    """LAYOUT-47, SUFFIX-10, SUFFIX-12: one declaration covers its own rule only, and a stale one is one finding."""
    config = ONE_JOB + "".join(declaring(path, rule=rule) for path, rule in declarations)
    assert red(build_repo({**frontend(*tracked), CONFIG: config}), RULE) == expected


def test_every_finding_prints_sorted_by_path_with_its_own_kind_fact_and_fix(build_repo, capsys):
    """LAYOUT-7, LAYOUT-28, LAYOUT-48, ROW-7, ROW-14: both frontend cases and a Python stray in one run, one per file,
    each its own row and fix line."""
    files = {
        **frontend("packages/ui/package.json", "packages/ui/src/z.svelte.test.ts", "packages/ui/src/spec.spec.ts", "tests/helpers.py"),
        **PYTHON_PROJECT,
    }
    repo = build_repo(files)
    code, output = check_output(capsys, repo)
    spec, test, stray = found = rule_findings(repo, RULE)
    assert (code, [finding.location for finding in found]) == (1, ["packages/ui/src/spec.spec.ts", "packages/ui/src/z.svelte.test.ts", "tests/helpers.py"])
    assert_row_is_kind(output, spec, FindingEvent.MISPLACED, f"packages/ui/{layout.END_TO_END_DIRECTORY}/")
    assert_row_is_kind(output, test, FindingEvent.MISPLACED, "packages/ui/src", "z")
    assert_row_is_kind(output, stray, FindingEvent.STRAY)
    assert (len({finding.fix for finding in found}), [rows_of(output, finding)[0].fix_follows for finding in found]) == (3, [True] * 3)


def test_an_empty_base_is_not_described_as_a_name_with_nothing_in_it(build_repo, capsys):
    """LAYOUT-28, LAYOUT-44, LAYOUT-48, ROW-7, ROW-14: an empty base has a fact of its own."""
    repo = build_repo(frontend("src/.test.ts", "src/zz.test.ts"))
    assert_empty_name_has_its_own_fact(capsys, repo, RULE, "src/.test.ts", "src/zz.test.ts", "zz")


def test_a_root_spec_names_the_root_e2e_folder(build_repo, capsys):
    """LAYOUT-28, LAYOUT-48, ROW-7, ROW-14: the fact of a root spec names the root project's `e2e/` folder."""
    finding = assert_prints_as_kind(capsys, build_repo(frontend("src/a.spec.ts")), RULE, FindingEvent.MISPLACED, f"{layout.END_TO_END_DIRECTORY}/")
    assert finding.path == "src/a.spec.ts"


def test_a_test_with_no_subject_in_the_root_folder_does_not_name_the_folder_as_a_dot(build_repo, capsys):
    """LAYOUT-48, LAYOUT-49, ROW-7, ROW-14: a test in the root folder is told apart from one in `src`."""
    repo = build_repo(frontend("x.test.ts", "src/x.test.ts"))
    output = check_output(capsys, repo)[1]
    found = {finding.path: finding for finding in rule_findings(repo, RULE)}
    assert_row_is_kind(output, found["x.test.ts"], FindingEvent.MISPLACED, "x")
    assert_row_is_kind(output, found["src/x.test.ts"], FindingEvent.MISPLACED, "src")
    assert re.search(r"(?<!\w)\.(?!\w)", found["x.test.ts"].fact) is None
