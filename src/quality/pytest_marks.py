"""The pytest marks a Python test module names, read from its syntax, and the marks a project registers. No repository code
runs, so a mark that only a run applies is not seen."""

import ast
from collections.abc import Iterator

from quality import clones, repository

IMPORTS_ADAPTER = "imports_adapter"
MARK_PREFIX = "pytest.mark."
MODULE_MARKS = "pytestmark"
NO_MARKS: frozenset[str] = frozenset()


def _marks_named(node: ast.expr, aliases: dict[str, frozenset[str]]) -> frozenset[str]:
    """The marks an expression names: a pytest mark bare or called, a module name bound to one, or a list or tuple of them."""
    while isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.List | ast.Tuple):
        return NO_MARKS.union(*(_marks_named(element, aliases) for element in node.elts))
    if isinstance(node, ast.Name):
        return aliases.get(node.id, NO_MARKS)
    written = _dotted_name(node)
    return frozenset({written.removeprefix(MARK_PREFIX)}) if written.startswith(MARK_PREFIX) else NO_MARKS


def _dotted_name(node: ast.expr) -> str:
    """`a.b.c` for a chain of attributes on a name, else an empty string; walks the chain, so its depth costs no recursion."""
    names: list[str] = []
    while isinstance(node, ast.Attribute):
        names.append(node.attr)
        node = node.value
    return ".".join([node.id, *reversed(names)]) if isinstance(node, ast.Name) else ""


def _bound_marks(body: list[ast.stmt], aliases: dict[str, frozenset[str]]) -> Iterator[tuple[str, frozenset[str], int]]:
    """Each single-name assignment of the body, its name, the marks its value names and its line."""
    for statement in body:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
            yield statement.targets[0].id, _marks_named(statement.value, aliases), statement.lineno


def _aliases(tree: ast.Module) -> dict[str, frozenset[str]]:
    return {name: marks for name, marks, _ in _bound_marks(tree.body, {}) if marks and name != MODULE_MARKS}


def module_marks(tree: ast.Module) -> dict[str, int]:
    """Each mark the module's own `pytestmark` names, with the line of the first statement that names it."""
    marks: dict[str, int] = {}
    for name, named, line in _bound_marks(tree.body, _aliases(tree)):
        if name == MODULE_MARKS:
            for mark in sorted(named):
                marks.setdefault(mark, line)
    return marks


def marks_by_test(tree: ast.Module) -> Iterator[frozenset[str]]:
    """The marks that reach each test function: its own, its enclosing classes' and the module's `pytestmark`."""
    aliases = _aliases(tree)
    pending: list[tuple[ast.AST, frozenset[str]]] = [(tree, frozenset(module_marks(tree)))]
    while pending:
        node, inherited = pending.pop()
        for child in ast.iter_child_nodes(node):
            marks = inherited
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                marks = inherited.union(*(_marks_named(decorator, aliases) for decorator in child.decorator_list))
            if isinstance(child, ast.ClassDef):
                marks = marks.union(*(named for name, named, _ in _bound_marks(child.body, aliases) if name == MODULE_MARKS))
            if clones.is_test_function(child):
                yield marks
            pending.append((child, marks))


def registers(pyproject: str, mark: str) -> bool:
    """Whether the project's `[tool.pytest.ini_options] markers` list names `mark`, alone or before a `:` and its text.
    A registration only a conftest hook, pytest's native table or another config file makes is not read."""
    try:
        document = repository.parse_toml(pyproject)
    except repository.TomlError:
        return False
    table: object = document
    for key in ("tool", "pytest", "ini_options"):
        table = table.get(key) if isinstance(table, dict) else None
    markers = table.get("markers") if isinstance(table, dict) else None
    return isinstance(markers, list) and any(isinstance(entry, str) and entry.partition(":")[0].strip() == mark for entry in markers)


def carries_registered_mark(source: str | bytes, pyproject: str | None) -> bool:
    """Whether the module's `pytestmark` names `imports_adapter` and its project's `pyproject.toml` registers it."""
    if pyproject is None or not registers(pyproject, IMPORTS_ADAPTER):
        return False
    try:
        return IMPORTS_ADAPTER in module_marks(ast.parse(source))
    except (SyntaxError, ValueError):
        return False
