"""The pytest marks a test module names, read from its syntax, and the marks a project registers."""

import ast
import textwrap

import pytest

from quality.pytest_marks import IMPORTS_ADAPTER, imports_adapter_line, marks_by_test, module_marks, registers

MODULE_MARKS = [
    pytest.param("pytestmark = pytest.mark.imports_adapter\n", {IMPORTS_ADAPTER: 1}, id="bare"),
    pytest.param("pytestmark = pytest.mark.imports_adapter()\n", {IMPORTS_ADAPTER: 1}, id="called"),
    pytest.param("pytestmark = [pytest.mark.imports_adapter]\n", {IMPORTS_ADAPTER: 1}, id="alone in a list"),
    pytest.param("pytestmark = (pytest.mark.imports_adapter,)\n", {IMPORTS_ADAPTER: 1}, id="alone in a tuple"),
    pytest.param("import pytest\n\npytestmark = [pytest.mark.anyio, pytest.mark.imports_adapter]\n", {"anyio": 3, IMPORTS_ADAPTER: 3}, id="beside another mark"),
    pytest.param("adapter = pytest.mark.imports_adapter\npytestmark = adapter\n", {IMPORTS_ADAPTER: 2}, id="through a module-level alias"),
    pytest.param("adapter = pytest.mark.imports_adapter\npytestmark = [adapter]\n", {IMPORTS_ADAPTER: 2}, id="an alias in a list"),
    pytest.param("pytestmark = pytest.mark.anyio\n", {"anyio": 1}, id="another mark only"),
    pytest.param("x = 1\n", {}, id="no pytestmark"),
    pytest.param("pytestmark = pytest.mark.imports_adapter\npytestmark = pytest.mark.anyio\n", {"anyio": 2}, id="a later plain rebind drops the earlier mark"),
    pytest.param("pytestmark = pytest.mark.anyio\npytestmark: list = [pytest.mark.imports_adapter]\n", {IMPORTS_ADAPTER: 2}, id="an annotated last binding decides"),
    pytest.param("pytestmark = pytest.mark.imports_adapter\nif X:\n    pytestmark = pytest.mark.anyio\n", {"anyio": 3}, id="a binding inside an if after a plain one decides"),
    pytest.param("try:\n    pytestmark = pytest.mark.anyio\nexcept E:\n    pytestmark = pytest.mark.slow\n", {"slow": 4}, id="the last binding of a try decides"),
    pytest.param("pytestmark = pytest.mark.imports_adapter\npytestmark += [pytest.mark.anyio]\n", {}, id="an augmented binding gives no marks"),
    pytest.param("pytestmark = pytest.mark.imports_adapter\npytestmark = make_marks()\n", {}, id="a last binding that is no mark expression gives no marks"),
    pytest.param("pytestmark: int\npytestmark = pytest.mark.anyio\n", {"anyio": 2}, id="an annotation without a value binds nothing"),
    pytest.param("def f():\n    pytestmark = pytest.mark.anyio\npytestmark = pytest.mark.slow\n", {"slow": 3}, id="a binding inside a function is no module binding"),
]


@pytest.mark.parametrize(("source", "expected"), MODULE_MARKS)
def test_a_module_level_pytestmark_names_each_mark_it_holds_at_its_line(source, expected):
    """The forms of a module's `pytestmark`: bare or called, alone, in a list or tuple, or through an alias."""
    assert module_marks(ast.parse(source)) == expected


NOT_MODULE_MARKS = [
    pytest.param("@pytest.mark.imports_adapter\ndef test_a(): ...\n", id="a test mark"),
    pytest.param("@pytest.mark.imports_adapter\nclass TestA:\n    def test_a(self): ...\n", id="a class decorator"),
    pytest.param("class TestA:\n    pytestmark = pytest.mark.imports_adapter\n", id="a class pytestmark"),
    pytest.param("adapter = pytest.mark.imports_adapter\n", id="an alias nothing assigns to pytestmark"),
    pytest.param("marks = pytest.mark.imports_adapter\n", id="another module name"),
]


@pytest.mark.parametrize("source", NOT_MODULE_MARKS)
def test_only_the_module_level_pytestmark_is_the_modules_mark(source):
    assert module_marks(ast.parse(source)) == {}


def test_a_test_carries_its_own_marks_its_classes_and_the_modules():
    tree = ast.parse(
        "pytestmark = pytest.mark.slow\n@pytest.mark.skip\nclass TestA:\n    pytestmark = pytest.mark.imports_adapter\n"
        "    @pytest.mark.xfail\n    def test_a(self): ...\n"
    )
    assert list(marks_by_test(tree)) == [{"slow", "skip", IMPORTS_ADAPTER, "xfail"}]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param("pytestmark = pytest.mark.slow\n    pytestmark = pytest.mark.skip\n", {"skip"}, id="a later plain rebind drops the earlier mark"),
        pytest.param("pytestmark = pytest.mark.slow\n    pytestmark: list = [pytest.mark.skip]\n", {"skip"}, id="an annotated last binding decides"),
        pytest.param("pytestmark = pytest.mark.slow\n    if X:\n        pytestmark = pytest.mark.skip\n", {"skip"}, id="a binding inside an if decides"),
        pytest.param("pytestmark = pytest.mark.slow\n    pytestmark += [pytest.mark.skip]\n", set(), id="an augmented binding gives no marks"),
        pytest.param("pytestmark = pytest.mark.slow\n    pytestmark = make_marks()\n", set(), id="a last binding that is no mark expression gives no marks"),
        pytest.param("pytestmark = pytest.mark.slow\n", {"slow"}, id="a single binding"),
    ],
)
def test_the_last_pytestmark_binding_of_a_class_body_decides(body, expected):
    tree = ast.parse(f"class TestA:\n    {body}    def test_a(self): ...\n")
    assert list(marks_by_test(tree)) == [expected]


REBINDING_FORMS = [
    pytest.param("pytestmark = other = pytest.mark.anyio", {"anyio"}, id="a chained assignment decides"),
    pytest.param("other = pytestmark = [pytest.mark.anyio]", {"anyio"}, id="a chained assignment decides with the name last"),
    pytest.param("(pytestmark := pytest.mark.anyio)", {"anyio"}, id="a walrus decides by its value"),
    pytest.param("f = lambda: (pytestmark := pytest.mark.anyio)", {"imports_adapter"}, id="a lambda walrus binds only the lambda"),
    pytest.param("@(pytestmark := pytest.mark.anyio)\ndef f(): ...", {"anyio"}, id="a decorator walrus decides"),
    pytest.param("def f(a=(pytestmark := pytest.mark.anyio)): ...", {"anyio"}, id="a default walrus decides"),
    pytest.param("class C((pytestmark := pytest.mark.anyio)): ...", {"anyio"}, id="a base walrus decides"),
    pytest.param(
        "(pytestmark := pytest.mark.anyio) if (pytestmark := pytest.mark.imports_adapter) else None",
        set(),
        id="two walruses of a conditional run out of source order and give no marks",
    ),
    pytest.param(
        "@(pytestmark := pytest.mark.imports_adapter)\ndef f(a=(pytestmark := pytest.mark.anyio)): ...",
        set(),
        id="a decorator walrus beside a default walrus gives no marks",
    ),
    pytest.param(
        "@(pytestmark := pytest.mark.imports_adapter)\nclass C((pytestmark := pytest.mark.anyio)): ...",
        set(),
        id="a decorator walrus beside a base walrus gives no marks",
    ),
    pytest.param(
        "[(pytestmark := pytest.mark.anyio) for _ in (pytestmark := pytest.mark.imports_adapter)]",
        set(),
        id="a comprehension element walrus beside its iterable walrus gives no marks",
    ),
    pytest.param(
        "other[(pytestmark := pytest.mark.imports_adapter)] = (pytestmark := pytest.mark.anyio)",
        set(),
        id="a target walrus beside a value walrus gives no marks",
    ),
    pytest.param("(pytestmark := make())", set(), id="a walrus of no mark gives no marks"),
    pytest.param("pytestmark = print((pytestmark := pytest.mark.anyio))", set(), id="an assignment binds after the walrus in its call value"),
    pytest.param("pytestmark = [(pytestmark := pytest.mark.anyio)]", set(), id="an assignment binds after the walrus in its list value and reads the list"),
    pytest.param("pytestmark: list = print((pytestmark := pytest.mark.anyio))", set(), id="an annotated assignment binds after the walrus in its value"),
    pytest.param("for pytestmark in (pytestmark := pytest.mark.anyio):\n    pass", set(), id="a for target binds after the walrus in its iterable"),
    pytest.param("with (pytestmark := pytest.mark.anyio) as pytestmark:\n    pass", set(), id="a with target binds after the walrus in its context expression"),
    pytest.param("pytestmark, other = make(), 1", set(), id="a tuple target gives no marks"),
    pytest.param("*pytestmark, other = make()", set(), id="a star target gives no marks"),
    pytest.param("for pytestmark in marks:\n    pass", set(), id="a for target gives no marks"),
    pytest.param("with ctx() as pytestmark:\n    pass", set(), id="a with target gives no marks"),
    pytest.param("import foo as pytestmark", set(), id="an import alias gives no marks"),
    pytest.param("from foo import bar as pytestmark", set(), id="an import-from alias gives no marks"),
    pytest.param("def pytestmark(): ...", set(), id="a function definition gives no marks"),
    pytest.param("class pytestmark: ...", set(), id="a class definition gives no marks"),
    pytest.param("match x:\n    case [*pytestmark]:\n        pass", set(), id="a match star capture gives no marks"),
    pytest.param("match x:\n    case {**pytestmark}:\n        pass", set(), id="a match mapping rest gives no marks"),
    pytest.param("del pytestmark", set(), id="a deletion gives no marks"),
    pytest.param("try:\n    pass\nexcept E as pytestmark:\n    pass", set(), id="an except name gives no marks"),
    pytest.param("match x:\n    case pytestmark:\n        pass", set(), id="a match capture gives no marks"),
    pytest.param("other = 1\nother.pytestmark = 2\nx = [pytestmark for pytestmark in y]", {"imports_adapter"}, id="no binding of the name in this scope leaves the mark"),
]


@pytest.mark.parametrize(("rebinding", "expected"), REBINDING_FORMS)
def test_any_later_binding_of_a_module_pytestmark_decides_as_in_pytest(rebinding, expected):
    tree = ast.parse(f"pytestmark = pytest.mark.imports_adapter\n{rebinding}\n")
    assert set(module_marks(tree)) == expected


@pytest.mark.parametrize(("rebinding", "expected"), REBINDING_FORMS)
def test_any_later_binding_of_a_class_pytestmark_decides_as_in_pytest(rebinding, expected):
    indented = textwrap.indent(rebinding, "    ")
    tree = ast.parse(f"class TestA:\n    pytestmark = pytest.mark.imports_adapter\n{indented}\n    def test_a(self): ...\n")
    assert list(marks_by_test(tree)) == [expected]


def test_an_alias_rebound_to_no_mark_gives_no_marks():
    tree = ast.parse("a = pytest.mark.imports_adapter\na = 1\npytestmark = a\n")
    assert module_marks(tree) == {}


REGISTRATIONS = [
    pytest.param(f'[tool.pytest.ini_options]\nmarkers = ["{IMPORTS_ADAPTER}"]\n', True, id="the name alone"),
    pytest.param(f'[tool.pytest.ini_options]\nmarkers = ["unit: fast", "{IMPORTS_ADAPTER}: reads a service"]\n', True, id="the name with a description"),
    pytest.param('[tool.pytest.ini_options]\nmarkers = ["unit: fast"]\n', False, id="another mark"),
    pytest.param(f'[tool.pytest.ini_options]\nmarkers = ["{IMPORTS_ADAPTER}_more: other"]\n', False, id="a longer name"),
    pytest.param(f'[tool.pytest.ini_options]\nmarkers = "{IMPORTS_ADAPTER}: one string"\n', False, id="a string where a list is the form"),
    pytest.param("[tool.pytest.ini_options]\n", False, id="no markers"),
    pytest.param("[project]\nname = 'shop'\n", False, id="no pytest table"),
    pytest.param(f'[tool.pytest]\nmarkers = ["{IMPORTS_ADAPTER}"]\n', False, id="pytest's native table is not read"),
    pytest.param("[tool.pytest.ini_options\n", False, id="a file that is no TOML"),
]


@pytest.mark.parametrize(("pyproject", "expected"), REGISTRATIONS)
def test_a_project_registers_a_mark_in_the_markers_of_its_pytest_ini_options(pyproject, expected):
    assert registers(pyproject, IMPORTS_ADAPTER) is expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("pytestmark = pytest.mark.imports_adapter\n", 1, id="marked"),
        pytest.param(b"import pytest\n\npytestmark = [pytest.mark.imports_adapter]\n", 3, id="marked, read as bytes at its line"),
        pytest.param("x = 1\n", None, id="not marked"),
        pytest.param("pytestmark = pytest.mark.anyio\n", None, id="another mark"),
        pytest.param("@pytest.mark.imports_adapter\ndef test_a(): ...\n", None, id="a test mark"),
        pytest.param("pytestmark = pytest.mark.imports_adapter\npytestmark = pytest.mark.anyio\n", None, id="a later rebind drops it"),
        pytest.param("pytestmark = pytest.mark.anyio\npytestmark = pytest.mark.imports_adapter\n", 2, id="a later rebind brings it, at its line"),
        pytest.param("def broken(:  # imports_adapter\n", None, id="a module that does not parse"),
    ],
)
def test_the_registered_mark_of_a_module_is_found_at_the_line_of_its_pytestmark(source, expected):
    assert imports_adapter_line(source) == expected
