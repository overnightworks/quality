"""The test-layout rule maps every frontend test to the file it tests and keeps Playwright specs in `e2e/`."""

import pytest

from tests.support.check import hygiene_output
from tests.support.emergency import ONE_JOB
from tests.support.layout import CONFIG, GUIDANCE, case, declaring, empty_files, in_index, red

RULE = "test-layout"
PACKAGE_JSON = {"package.json": "{}\n"}
PYTHON_PROJECT = {"pyproject.toml": "", "src/p/x.py": ""}
SPEC_FIX = "or declare it in quality/config.toml with [[layout.allow]], its path, the rule test-layout and a reason."


def frontend(*paths: str, manifest: str = "package.json") -> dict[str, str]:
    """A repository tracking `manifest` (default: a root `package.json`) and the given paths, all empty."""
    return {**GUIDANCE, manifest: "{}\n", **empty_files(*paths)}


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        case("H1 a root project", frontend("src/a.ts", "src/a.test.ts"), []),
        case("H1 a member project", frontend("frontend/package.json", "frontend/x.ts", "frontend/x.test.ts", "frontend/y.test.ts"),
             ["frontend/y.test.ts"]),
        case("H2 no package.json", {**GUIDANCE, **empty_files("tests/x.test.mjs", "src/y.test.ts")}, []),
        case("H3 a package.json in the fixtures of a Python project",
             {**GUIDANCE, **PYTHON_PROJECT, **empty_files("tests/fixtures/app/package.json", "tests/fixtures/app/z.test.ts")}, []),
        case("H3 a pyproject.toml in the tests of a frontend project",
             frontend("frontend/package.json", "frontend/tests/fixtures/pyproject.toml", "frontend/tests/fixtures/src/p/x.py",
                      "frontend/tests/fixtures/tests/stray.txt"), []),
        case("H4 a spec in a member's e2e", frontend("packages/ui/package.json", "packages/ui/e2e/f.spec.ts", "e2e/f.spec.ts"), []),
        case("H4 a spec outside a member's e2e", frontend("packages/ui/package.json", "packages/ui/src/f.spec.ts"),
             ["packages/ui/src/f.spec.ts"]),
        case("H5 the aspects of a test",
             frontend("src/a.svelte.ts", "src/a.svelte.test.ts", "src/App.svelte", "src/App.test.ts", "src/x.ts",
                      "src/x.browser.test.ts", "src/x.test-d.ts", "src/x.spec-d.ts", "src/m.mjs", "src/m.test.mjs"), []),
        case("H6 a route test beside its page", frontend("src/page.test.ts", "src/+page.svelte"), []),
        case("H6 a route test beside only a page server", frontend("src/page.test.ts", "src/+page.server.ts"), []),
        case("H6 the other route files",
             frontend("src/layout.test.ts", "src/+layout.svelte", "src/error.test.ts", "src/+error.svelte",
                      "src/server.test.ts", "src/+server.ts", "src/hooks.server.test.ts", "src/hooks.server.ts"), []),
        case("H6 a route test alone", frontend("src/page.test.ts"), ["src/page.test.ts"]),
        case("H7 a prefix at a word boundary",
             frontend("src/AlbumCoverEditor.svelte", "src/AlbumCoverEditorLeaving.test.ts", "src/AlbumCoverEditor.leaving.test.ts",
                      "src/editor.ts", "src/editorUnloadGuard.test.ts", "src/detail.ts", "src/detail-back.test.ts",
                      "src/share.ts", "src/share_import.test.ts"), []),
        case("H7 no sibling for a prefix", frontend("src/detail-back.test.ts"), ["src/detail-back.test.ts"]),
        case("H7 a prefix that is no key", frontend("src/optionCard.ts", "src/optionDecision.test.ts"), ["src/optionDecision.test.ts"]),
        case("H8 no word boundary", frontend("src/editor.ts", "src/editorx.test.ts"), ["src/editorx.test.ts"]),
        case("H8 another case", frontend("src/editor.ts", "src/Editor.test.ts"), ["src/Editor.test.ts"]),
        case("H8 an empty base", frontend("src/editor.ts", "src/.test.ts"), ["src/.test.ts"]),
        case("H8 a leading underscore stays in the key", frontend("src/_helpers.ts", "src/_helpers.test.ts"), []),
        case("H9 a type test is no subject", frontend("src/a.test.ts", "src/a.test-d.ts"), ["src/a.test-d.ts", "src/a.test.ts"]),
        case("H9 a test component is no subject", frontend("src/Card.test.ts", "src/Card.test.svelte"), ["src/Card.test.ts"]),
        case("H9 a folder is no subject", frontend("src/a.test.ts", "src/a/b.ts"), ["src/a.test.ts"]),
        case("H9 data is a subject", frontend("src/schema.test.ts", "src/schema.json"), []),
        case("H10 specs at any depth of e2e", frontend("e2e/f.spec.ts", "e2e/sub/f.spec.ts"), []),
        case("H10 specs elsewhere", frontend("src/x.spec.ts", "tests/e2e/x.spec.ts", "src/a.spec.mjs"),
             ["src/a.spec.mjs", "src/x.spec.ts", "tests/e2e/x.spec.ts"]),
        case("H11 a test in e2e beside its subject", frontend("e2e/helpers.ts", "e2e/helpers.test.ts"), []),
        case("H11 a test in e2e alone", frontend("e2e/flow.test.ts"), ["e2e/flow.test.ts"]),
        case("H12 files no rule judges",
             frontend("e2e/helpers.ts", "src/lib/test-utils/x.ts", "src/tests/setup.ts", "src/__tests__/x.ts", "src/Card.test.svelte"), []),
        case("H13 one owner under a judged Python tests",
             {**frontend("tests/foo.test.ts", "tests/e2e/flow.spec.ts", "tests/support/x.test.ts", "tests/fixtures/a.test.ts",
                         "e2e/flow.spec.ts"), **PYTHON_PROJECT}, ["tests/foo.test.ts"]),
        case("H14 a Python project without a package leaves its tests to the frontend",
             frontend("tests/foo.test.ts", manifest="pyproject.toml") | PACKAGE_JSON, ["tests/foo.test.ts"]),
        case("H18 a Cargo tree gives no finding", frontend("crates/a/Cargo.toml", "crates/a/tests/y.rs"), []),
        case("H18 a package.json in a Cargo tree is a project",
             {**GUIDANCE, **empty_files("Cargo.toml", "tools/package.json", "tools/a.test.mjs")}, ["tools/a.test.mjs"]),
    ],
)
def test_the_sibling_and_the_e2e_folder_are_judged_per_frontend_project(build_repo, capsys, files, expected):
    """H1 to H14 and H18: only a judged test file in a frontend project is red, once, by its name."""
    assert red(capsys, build_repo(files), RULE) == expected


def test_a_symlink_is_judged_by_its_name_and_a_gitlink_is_never_judged_or_a_subject(build_repo, capsys):
    """H9 and H16: a link is never followed; a symlink is a subject by its own name, a gitlink is a folder."""
    repo = build_repo(frontend("src/real.ts", "src/b.test.ts", "src/c.test.ts", "src/d.test.ts"))
    (repo / "src" / "a.ts").symlink_to("real.ts")
    (repo / "src" / "x.test.ts").symlink_to("real.ts")
    in_index(repo, "add", "--", "src/a.ts", "src/x.test.ts")
    in_index(repo, "update-index", "--add", "--cacheinfo", f"160000,{'1' * 40},src/b")
    in_index(repo, "update-index", "--add", "--cacheinfo", f"160000,{'1' * 40},src/y.test.ts")
    assert red(capsys, repo, RULE) == ["src/b.test.ts", "src/c.test.ts", "src/d.test.ts", "src/x.test.ts"]


def test_a_symlinked_subject_maps_a_test(build_repo, capsys):
    """H9: `a.test.ts` beside a symlink `a.ts` is clean."""
    repo = build_repo(frontend("src/real.ts", "src/a.test.ts"))
    (repo / "src" / "a.ts").symlink_to("real.ts")
    in_index(repo, "add", "--", "src/a.ts")
    assert red(capsys, repo, RULE) == []


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
def test_a_declaration_exempts_a_frontend_test_from_rules_six_and_seven(build_repo, capsys, tracked, declarations, expected):
    """H15: one declaration covers its own rule only, and a stale one is one finding."""
    config = ONE_JOB + "".join(declaring(path, rule=rule) for path, rule in declarations)
    assert red(capsys, build_repo({**frontend(*tracked), CONFIG: config}), RULE) == expected


def test_every_finding_prints_sorted_by_path_with_its_own_reason_and_fix(build_repo, capsys):
    """H17: both frontend cases and a Python finding in one run, one per file, each with its reason and fix."""
    files = {
        **frontend("packages/ui/package.json", "packages/ui/src/z.svelte.test.ts", "packages/ui/src/spec.spec.ts", "tests/helpers.py"),
        **PYTHON_PROJECT,
    }
    code, output = hygiene_output(capsys, build_repo(files))
    lines = [line for line in output.splitlines() if line.startswith(f"{RULE} ")]
    assert code == 1
    assert [line.split()[1] for line in lines] == ["packages/ui/src/spec.spec.ts", "packages/ui/src/z.svelte.test.ts", "tests/helpers.py"]
    assert "outside packages/ui/e2e/" in lines[0]
    assert "no file in packages/ui/src is named z or a word prefix of it" in lines[1]
    assert f"  Fix: move it into packages/ui/e2e/, or, if it is a Vitest test, name it `<subject>.test.<suffix>` beside its subject, {SPEC_FIX}" in output
    assert f"  Fix: move it beside the file it tests and name it `<subject>[<Aspect>].svelte.test.ts`, {SPEC_FIX}" in output


def test_an_empty_base_says_it_is_empty(build_repo, capsys):
    """H8: the reason never reads "a word prefix of" an empty base."""
    output = hygiene_output(capsys, build_repo(frontend("src/.test.ts")))[1]
    assert "its name before the first `.` is empty" in output


def test_a_root_spec_names_the_root_e2e_folder(build_repo, capsys):
    """H4: the root project's folder is `e2e/`."""
    output = hygiene_output(capsys, build_repo(frontend("src/a.spec.ts")))[1]
    assert "outside e2e/" in output
    assert "move it into e2e/, or" in output
