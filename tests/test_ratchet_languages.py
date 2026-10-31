"""#119: the languages the size rule measures and the area a file belongs to."""

import pytest

from quality.ratchet import Area, Language, area_of, language_lines, measure
from tests.support.source_code import long_file, minified_bundle, typescript_file

MIXED_TREE = {
    "src/app.py": long_file(100),
    "src/frontend/src/lib/api/types.ts": typescript_file(50),
    "src/frontend/src/Card.svelte": typescript_file(20),
    "src/frontend/src/legacy.js": typescript_file(5),
    "scripts/deploy_app.sh": "# Deploys the app.\n" + "echo hi\n" * 399,
    "src/frontend/src/app.css": "a { color: red; }\n" * 400,
}
@pytest.mark.parametrize(
    ("files", "languages"),
    [
        ({"src/a.py": long_file(3)}, [Language.PYTHON]),
        (MIXED_TREE, [Language.PYTHON, Language.TYPESCRIPT, Language.SVELTE, Language.JAVASCRIPT]),
        ({"src/a.svelte": long_file(5), "src/b.js": long_file(5), "src/c.vue": long_file(5)}, [Language.JAVASCRIPT, Language.SVELTE, Language.VUE]),
        ({"src/a.py": long_file(3), "src/dist/index.js": minified_bundle()}, [Language.PYTHON, Language.JAVASCRIPT]),
    ],
    ids=[
        "#119 cell 10 a Python-only tree names Python",
        "#119 cell 11 a mixed tree lists its languages largest first, shell and CSS absent",
        "#119 cell 11 equal lines are ordered by name",
        "#119 cell 12 a minified bundle's language is listed",
    ],
)
def test_the_measured_languages_are_listed_largest_first_and_by_name_on_a_tie(files, languages):
    """#119 E5: the scoreboard's reading of a tree names each measured language."""
    assert list(language_lines(measure({path: text.encode() for path, text in files.items()}))) == languages


@pytest.mark.parametrize(
    ("path", "area"),
    [
        ("src/a.test.ts", Area.TESTS),
        pytest.param("src/a.test-d.ts", Area.TESTS, id="H21 test-d"),
        pytest.param("src/a.spec-d.ts", Area.TESTS, id="H21 spec-d"),
        ("e2e/login.spec.ts", Area.TESTS),
        ("src/decision.svelte.test.ts", Area.TESTS),
        ("src/__tests__/x.ts", Area.TESTS),
        ("tests/Harness.svelte", Area.TESTS),
        ("frontend/e2e/helpers.ts", Area.PRODUCTION),
        ("src/lib/test-utils/x.ts", Area.PRODUCTION),
        ("src/test_x.ts", Area.PRODUCTION),
        ("src/Card.test.svelte", Area.PRODUCTION),
        ("src/__tests__/Card.svelte", Area.PRODUCTION),
    ],
)
def test_the_name_rule_of_a_typescript_or_javascript_file_is_the_runners_defaults(path, area):
    """#119 E3, cell 9: `*.test.*`, `*.spec.*` and `__tests__/` for TS and JS; Svelte and Vue by directory only."""
    assert area_of(f"src/{path}") is area
