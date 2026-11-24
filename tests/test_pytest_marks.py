"""The pytest marks a test module names, read from its syntax, and the marks a project registers."""

import ast

import pytest

from quality.pytest_marks import IMPORTS_ADAPTER, carries_registered_mark, marks_by_test, module_marks, registers

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
    ("source", "pyproject", "expected"),
    [
        pytest.param("pytestmark = pytest.mark.imports_adapter\n", REGISTRATIONS[0].values[0], True, id="marked and registered"),
        pytest.param("pytestmark = pytest.mark.imports_adapter\n", REGISTRATIONS[2].values[0], False, id="marked, not registered"),
        pytest.param("pytestmark = pytest.mark.imports_adapter\n", None, False, id="marked, no project file"),
        pytest.param("x = 1\n", REGISTRATIONS[0].values[0], False, id="registered, not marked"),
        pytest.param("def broken(:\n", REGISTRATIONS[0].values[0], False, id="a module that does not parse"),
    ],
)
def test_a_module_carries_the_registered_mark_when_it_names_it_and_its_project_registers_it(source, pyproject, expected):
    assert carries_registered_mark(source, pyproject) is expected
