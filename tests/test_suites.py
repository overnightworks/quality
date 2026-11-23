from collections.abc import Mapping

import pytest

from quality.suites import Layer, Suites, count_suites

PYTHON_TEST = "def test_a():\n    pass\n"
SCRIPT_TEST = "test('a', () => {});\n"
UNIT, INTEGRATION, FLOW, LIVE = Layer.UNIT, Layer.INTEGRATION, Layer.FLOW, Layer.LIVE


def counted(files: Mapping[str, str | bytes]) -> Suites:
    sources = {path: content.encode() if isinstance(content, str) else content for path, content in files.items()}
    return count_suites(list(files), sources)


def python_counts(source: str) -> tuple[int, int, int]:
    found = counted({"tests/test_a.py": source})
    return found.layers[UNIT], found.skipped, found.fixme


def script_counts(source: str) -> tuple[int, int, int]:
    found = counted({"web/a.test.ts": source})
    return found.layers[UNIT], found.skipped, found.fixme


@pytest.mark.parametrize(
    ("path", "manifest", "source", "layer"),
    [
        ("tests/test_a.py", None, PYTHON_TEST, UNIT),
        ("tests/e2e/test_a.py", None, PYTHON_TEST, FLOW),
        ("tests/e2e/live/test_a.py", None, PYTHON_TEST, LIVE),
        ("api/tests/e2e/test_a.py", "api/pyproject.toml", PYTHON_TEST, FLOW),
        ("api/tests/e2e/live/test_a.py", "api/pyproject.toml", PYTHON_TEST, LIVE),
        ("web/src/a.test.ts", "web/package.json", SCRIPT_TEST, UNIT),
        ("web/e2e/a.spec.ts", "web/package.json", SCRIPT_TEST, FLOW),
        ("web/e2e/live/a.spec.ts", "web/package.json", SCRIPT_TEST, LIVE),
    ],
    ids=[
        "unit under the root tests", "flow under the root e2e", "live under the root live", "flow under a project's e2e",
        "live under a project's live", "unit beside its source", "flow in a frontend project's e2e", "live in a frontend project's live",
    ],
)
def test_a_test_is_counted_in_the_layer_its_path_names(path, manifest, source, layer):
    """FLEET-121: the path names Live and Flow before any mark is read."""
    found = counted({path: source, **({manifest: ""} if manifest else {})})
    assert {each: found.layers[each] for each in Layer} == {each: int(each is layer) for each in Layer}


@pytest.mark.parametrize(
    ("source", "tests"),
    [
        ("test('a', () => {});", 1),
        ("it('a', () => {});", 1),
        ("test(`a ${b}`, () => {});", 1),
        ("it.skip('a', () => {});", 1),
        ("test.only('a', () => {});", 1),
        ("test.fixme('a', () => {});", 1),
        ("it.concurrent('a', () => {});", 1),
        ("it.skipIf(c)('a', () => {});", 1),
        ("it.runIf(c)('a', () => {});", 1),
        ("it.each([1, 2])('a', () => {});", 1),
        ("test.describe('d', () => { test('a', () => {}); });", 1),
        ("test('a', async () => { await test.step('s', async () => {}); });", 1),
        ("test.use({ locale: 'de' });", 0),
        ("beforeEach(() => {});", 0),
        ("x.test('a', () => {});", 0),
        ("test.skip(browserName === 'x', 'why');", 0),
        ("test.describe.configure({ mode: 'serial' });", 0),
    ],
    ids=[
        "test", "it", "template title", "skip", "only", "fixme", "concurrent", "skipIf", "runIf", "each", "inside a describe",
        "step is no test", "use is no test", "hook is no test", "member call is no test", "runtime skip is no test", "configure is no test",
    ],
)
def test_a_script_call_is_a_test_only_in_the_forms_the_standard_names(source, tests):
    """FLEET-120: a call of it or test, bare or with a modifier, whose first argument is a string or a template."""
    assert script_counts(source)[0] == tests


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("@pytest.mark.skip\ndef test_a(): ...", (1, 1, 0)),
        ('@pytest.mark.skip(reason="x")\ndef test_a(): ...', (1, 1, 0)),
        ('@pytest.mark.skipif(True, reason="x")\ndef test_a(): ...', (1, 1, 0)),
        ("@pytest.mark.xfail\ndef test_a(): ...", (1, 0, 1)),
        ("@pytest.mark.slow\ndef test_a(): ...", (1, 0, 0)),
        ("@pytest.mark.skip\nclass TestA:\n    def test_a(self): ...\n    def test_b(self): ...", (2, 2, 0)),
        ("pytestmark = pytest.mark.skip\ndef test_a(): ...\ndef test_b(): ...", (2, 2, 0)),
        ("pytestmark = [pytest.mark.slow, pytest.mark.xfail]\ndef test_a(): ...", (1, 0, 1)),
        ("pytestmark = (pytest.mark.skipif(True, reason='x'),)\ndef test_a(): ...", (1, 1, 0)),
        ("slow = pytest.mark.skipif(True, reason='x')\n@slow\ndef test_a(): ...", (1, 1, 0)),
        ("@pytest.mark.skip\n@pytest.mark.xfail\ndef test_a(): ...", (1, 1, 1)),
        ("@pytest.mark.parametrize('x', [1, 2, 3])\ndef test_a(x): ...", (1, 0, 0)),
        ("def helper(): ...\ndef test_a(): ...", (1, 0, 0)),
    ],
    ids=[
        "bare skip", "called skip", "skipif", "xfail is fixme", "other mark", "class mark", "module mark", "list of marks", "tuple of marks",
        "module alias", "skip and xfail count in both", "parametrized counts once", "helper is no test",
    ],
)
def test_a_python_test_counts_the_skip_and_xfail_marks_that_reach_it(source, expected):
    """FLEET-120, FLEET-122, FLEET-123: a test function counts once, and each skip or xfail mark that reaches it counts."""
    assert python_counts(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("it.skip('a', f);", (1, 1, 0)),
        ("it.skipIf(c)('a', f);", (1, 1, 0)),
        ("it.runIf(c)('a', f);", (1, 0, 0)),
        ("test.fixme('a', f);", (1, 0, 1)),
        ("describe.skip('d', () => { it('a', f); it('b', f); });", (2, 2, 0)),
        ("describe.skipIf(c)('d', () => { it('a', f); });", (1, 1, 0)),
        ("test.describe.skip('d', () => { test('a', f); });", (1, 1, 0)),
        ("test.describe.fixme('d', () => { test('a', f); test('b', f); });", (2, 0, 2)),
        ("test('a', async () => { test.skip(name === 'x', 'why'); test.skip(other); });", (1, 1, 0)),
        ("test.describe('d', () => { test.skip(c); test('a', f); test('b', f); });", (2, 2, 0)),
        ("test.describe.fixme('d', () => { test.skip('a', f); });", (1, 1, 1)),
        ("test.skip(c, 'why');\ntest('a', f);\ntest('b', f);", (2, 2, 0)),
        ("test.fixme(c);\ntest.describe('d', () => { test('a', f); });", (1, 0, 1)),
        ("test.describe('d', () => { test('a', f); });\ntest('b', f);", (2, 0, 0)),
    ],
    ids=[
        "skip", "skipIf", "runIf is no mark", "fixme", "describe skip", "describe skipIf", "playwright describe skip",
        "playwright describe fixme", "runtime skip marks its test once", "runtime skip in a describe marks every test",
        "both marks", "file-level skip marks every test of the file", "file-level fixme marks a test in a describe", "a mark stays in its describe",
    ],
)
def test_a_script_test_counts_the_skip_and_fixme_marks_that_reach_it(source, expected):
    """FLEET-122, FLEET-123: a skip or fixme on the call, an enclosing describe or a runtime call counts each test it reaches."""
    assert script_counts(source) == expected


@pytest.mark.parametrize(
    ("path", "source", "layer", "skipped"),
    [
        ("tests/test_a.py", "@pytest.mark.integration\ndef test_a(): ...", INTEGRATION, 0),
        ("tests/test_a.py", "@pytest.mark.integration()\ndef test_a(): ...", INTEGRATION, 0),
        ("tests/test_a.py", "@pytest.mark.integration\nclass TestA:\n    def test_a(self): ...", INTEGRATION, 0),
        ("tests/test_a.py", "class TestA:\n    pytestmark = pytest.mark.integration\n    def test_a(self): ...", INTEGRATION, 0),
        ("tests/test_a.py", "pytestmark = [pytest.mark.slow, pytest.mark.integration]\ndef test_a(): ...", INTEGRATION, 0),
        ("tests/test_a.py", "pytestmark = (pytest.mark.integration,)\ndef test_a(): ...", INTEGRATION, 0),
        ("tests/test_a.py", "slow = pytest.mark.integration\n@slow\ndef test_a(): ...", INTEGRATION, 0),
        ("tests/test_a.py", "def test_a(): ...", UNIT, 0),
        ("tests/test_a.py", "@pytest.mark.slow\ndef test_a(): ...", UNIT, 0),
        ("tests/e2e/test_a.py", "@pytest.mark.integration\ndef test_a(): ...", FLOW, 0),
        ("tests/e2e/live/test_a.py", "@pytest.mark.integration\ndef test_a(): ...", LIVE, 0),
        ("tests/test_a.py", "@pytest.mark.skip\n@pytest.mark.integration\ndef test_a(): ...", INTEGRATION, 1),
        ("tests/test_a.py", "@pytest.mark.parametrize('x', [pytest.param(1, marks=pytest.mark.integration)])\ndef test_a(x): ...", UNIT, 0),
    ],
    ids=[
        "bare mark", "called mark", "class mark", "class pytestmark", "module pytestmark list", "module pytestmark tuple", "module alias",
        "unmarked is unit", "other mark is unit", "the e2e path wins", "the live path wins", "skipped and integration count in both",
        "a mark given only through pytest.param is not read",
    ],
)
def test_a_python_test_is_integration_when_an_integration_mark_reaches_it_and_the_path_has_not_named_its_layer(path, source, layer, skipped):
    """FLEET-121: the path names Live and Flow first; every other Python test is Integration when the mark reaches it, else Unit."""
    found = counted({path: source})
    assert ({each: found.layers[each] for each in Layer}, found.skipped) == ({each: int(each is layer) for each in Layer}, skipped)


def test_a_script_test_outside_the_flow_folder_is_a_unit_test():
    """FLEET-121: a script test has no integration mark."""
    found = counted({"web/a.test.ts": "describe('integration', () => {});\ntest('integration', () => {});", "web/package.json": ""})
    assert (found.layers[UNIT], found.layers[INTEGRATION]) == (1, 0)


def test_a_file_that_cannot_be_read_is_counted_apart_and_the_others_still_count():
    """FLEET-124: a Python file that does not parse, a script that is not UTF-8 and one the lexer refuses are not read."""
    found = counted({
        "tests/test_good.py": PYTHON_TEST,
        "tests/test_bad.py": "def (:\n",
        "web/good.test.ts": SCRIPT_TEST,
        "web/bytes.test.ts": b"\xff\xfe",
        "web/open.test.ts": "/* never closed",
    })
    assert (found.layers[UNIT], found.unread) == (2, 3)


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("@a" + ".b" * 400 + "\ndef test_a(): ...\n", id="a-deep-dotted-decorator"),
        pytest.param("x = a" + ".b" * 600 + "\ndef test_a(): ...\n", id="a-deep-dotted-expression"),
        pytest.param("@a" + "()" * 3000 + "\ndef test_a(): ...\n", id="a-deep-call-chain-decorator"),
        pytest.param("x = a" + "()" * 3000 + "\ndef test_a(): ...\n", id="a-deep-call-chain-expression"),
    ],
)
def test_a_deeply_nested_python_file_still_counts_its_tests(source):
    """FLEET-124: a file the reader cannot walk deeply never aborts the report; the others still count."""
    found = counted({"tests/test_deep.py": source, "tests/test_good.py": PYTHON_TEST})
    assert found.layers[UNIT] + found.unread >= 1
    assert found.layers[UNIT] >= 1


def test_a_script_whose_last_token_is_a_dot_still_counts_its_first_call():
    """FLEET-124: the first token has no token before it, so a trailing dot is no member access."""
    assert script_counts("test('a', f); x.")[0] == 1
