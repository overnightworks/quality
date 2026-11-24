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
    pytest.param("adapter = pytest.mark.imports_adapter\nadapter = pytest.mark.anyio\npytestmark = adapter\n", {}, id="an alias bound twice holds no marks"),
    pytest.param("if X:\n    adapter = pytest.mark.imports_adapter\npytestmark = adapter\n", {}, id="an alias bound inside an if holds no marks"),
    pytest.param("adapter = pytest.mark.imports_adapter\nfor adapter in marks:\n    pass\npytestmark = adapter\n", {}, id="an alias bound again by a loop holds no marks"),
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


CLASS_ALIAS_SOURCES = [
    pytest.param("m = pytest.mark.skip\nclass TestA:\n    m = pytest.mark.slow\n    pytestmark = m\n    def test_a(self): ...\n", id="a class pytestmark"),
    pytest.param("m = pytest.mark.skip\nclass TestA:\n    m = pytest.mark.slow\n    @m\n    def test_a(self): ...\n", id="a method decorator"),
]


@pytest.mark.parametrize("source", CLASS_ALIAS_SOURCES)
def test_a_name_a_class_rebinds_is_no_module_alias_inside_that_class(source):
    assert list(marks_by_test(ast.parse(source))) == [set()]


MARK_SLOW = "pytestmark = pytest.mark.slow"
BODY_MARKS = [
    pytest.param(MARK_SLOW, {"slow"}, id="one plain binding reads"),
    pytest.param("pytestmark: list = [pytest.mark.slow]", {"slow"}, id="one annotated binding reads"),
    pytest.param("pytestmark = (pytest.mark.slow, pytest.mark.skip)", {"slow", "skip"}, id="one binding of a tuple of marks reads"),
    pytest.param("pytestmark = other = pytest.mark.slow", {"slow"}, id="one chained binding reads"),
    pytest.param(f"def helper():\n    pytestmark = pytest.mark.skip\n{MARK_SLOW}", {"slow"}, id="a binding inside a function is another scope"),
    pytest.param(f"{MARK_SLOW}\n{MARK_SLOW}", set(), id="a second plain binding gives no marks"),
    pytest.param(f"{MARK_SLOW}\npytestmark: list = [pytest.mark.skip]", set(), id="a second annotated binding gives no marks"),
    pytest.param(f"{MARK_SLOW}\npytestmark += [pytest.mark.skip]", set(), id="an augmented binding gives no marks"),
    pytest.param(f"{MARK_SLOW}\n(pytestmark := pytest.mark.skip)", set(), id="a walrus binding gives no marks"),
    pytest.param(f"{MARK_SLOW}\nfor pytestmark in marks:\n    pass", set(), id="a for target gives no marks"),
    pytest.param(f"{MARK_SLOW}\nwith ctx() as pytestmark:\n    pass", set(), id="a with target gives no marks"),
    pytest.param(f"{MARK_SLOW}\nimport foo as pytestmark", set(), id="an import alias gives no marks"),
    pytest.param(f"{MARK_SLOW}\ndel pytestmark", set(), id="a deletion gives no marks"),
    pytest.param(f"{MARK_SLOW}\nmatch x:\n    case pytestmark:\n        pass", set(), id="a match capture gives no marks"),
    pytest.param(f"{MARK_SLOW}\nmatch x:\n    case {{**pytestmark}}:\n        pass", set(), id="a match mapping capture gives no marks"),
    pytest.param(f"{MARK_SLOW}\nmatch x:\n    case [*pytestmark]:\n        pass", set(), id="a match star capture gives no marks"),
    pytest.param(f"{MARK_SLOW}\ndef pytestmark(): ...", set(), id="a function definition gives no marks"),
    pytest.param(f"{MARK_SLOW}\nclass pytestmark: ...", set(), id="a class definition gives no marks"),
    pytest.param(f"{MARK_SLOW}\ntype pytestmark = int", set(), id="a type alias gives no marks"),
    pytest.param(f"{MARK_SLOW}\ntry:\n    pass\nexcept E as pytestmark:\n    pass", set(), id="an except name gives no marks"),
    pytest.param(f"{MARK_SLOW}\npytestmark, other = make(), 1", set(), id="a tuple target gives no marks"),
    pytest.param("if X:\n    pytestmark = pytest.mark.slow", set(), id="a binding inside an if gives no marks"),
    pytest.param("try:\n    pytestmark = pytest.mark.slow\nexcept E:\n    pass", set(), id="a binding inside a try gives no marks"),
    pytest.param("with ctx():\n    pytestmark = pytest.mark.slow", set(), id="a binding inside a with gives no marks"),
    pytest.param("for _ in xs:\n    pytestmark = pytest.mark.slow", set(), id="a binding inside a for gives no marks"),
    pytest.param("while X:\n    pytestmark = pytest.mark.slow", set(), id="a binding inside a while gives no marks"),
    pytest.param("match x:\n    case _:\n        pytestmark = pytest.mark.slow", set(), id="a binding inside a match gives no marks"),
    pytest.param("pytestmark = make_marks()", set(), id="a value that is no mark gives no marks"),
]


@pytest.mark.parametrize(("body", "expected"), BODY_MARKS)
def test_a_module_reads_its_pytestmark_only_when_one_direct_binding_holds_it(body, expected):
    assert set(module_marks(ast.parse(f"{body}\n"))) == expected


@pytest.mark.parametrize(("body", "expected"), BODY_MARKS)
def test_a_class_reads_its_pytestmark_only_when_one_direct_binding_holds_it(body, expected):
    tree = ast.parse(f"class TestA:\n{textwrap.indent(body, '    ')}\n    def test_a(self): ...\n")
    assert list(marks_by_test(tree)) == [expected]


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
        pytest.param("def broken(:  # imports_adapter\n", None, id="a module that does not parse"),
    ],
)
def test_the_registered_mark_of_a_module_is_found_at_the_line_of_its_pytestmark(source, expected):
    assert imports_adapter_line(source) == expected
