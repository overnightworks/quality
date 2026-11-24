"""The pytest marks a Python test module names, read from its syntax, and the marks a project registers. No repository code
runs, so a mark that only a run applies is not seen."""

import ast
from collections import Counter
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


def _bound_names(node: ast.AST) -> Iterator[str]:
    """The names a node binds, in any form: a target, walrus, `del`, import alias, `except` name, definition or match capture."""
    if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
        yield node.id
    elif isinstance(node, ast.alias):
        yield (node.asname or node.name).partition(".")[0]
    elif isinstance(node, ast.MatchMapping) and node.rest is not None:
        yield node.rest
    elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.ExceptHandler | ast.MatchAs | ast.MatchStar) and node.name is not None:
        yield node.name


def _scope_nodes(body: list[ast.stmt]) -> Iterator[ast.AST]:
    """Every node of a body's own scope, not the bodies of the functions and classes it defines; walks without recursion."""
    pending: list[ast.AST] = list(body)
    while pending:
        node = pending.pop()
        yield node
        is_definition = isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        pending.extend(child for child in ast.iter_child_nodes(node) if not (is_definition and isinstance(child, ast.stmt)))


def _single_assignments(body: list[ast.stmt]) -> dict[str, tuple[ast.expr, int]]:
    """Each name the body binds exactly once, in a plain or annotated assignment standing directly in it: its value and line."""
    bound = Counter(name for node in _scope_nodes(body) for name in _bound_names(node))
    assigned: dict[str, tuple[ast.expr, int]] = {}
    for statement in body:
        if isinstance(statement, ast.Assign):
            targets = statement.targets
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            targets = [statement.target]
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and bound[target.id] == 1:
                assigned[target.id] = (statement.value, statement.lineno)
    return assigned


def _aliases(tree: ast.Module) -> dict[str, frozenset[str]]:
    held = {name: _marks_named(value, {}) for name, (value, _) in _single_assignments(tree.body).items() if name != MODULE_MARKS}
    return {name: marks for name, marks in held.items() if marks}


def _own_marks(body: list[ast.stmt], aliases: dict[str, frozenset[str]]) -> dict[str, int]:
    """Each mark the body's one direct `pytestmark` binding names, with its line; any other shape names none."""
    value, line = _single_assignments(body).get(MODULE_MARKS, (None, 0))
    return {} if value is None else dict.fromkeys(sorted(_marks_named(value, aliases)), line)


def module_marks(tree: ast.Module) -> dict[str, int]:
    """Each mark the module's own `pytestmark` names, with the line of the binding."""
    return _own_marks(tree.body, _aliases(tree))


def marks_by_test(tree: ast.Module) -> Iterator[frozenset[str]]:
    """The marks that reach each test function: its own, its enclosing classes' and the module's `pytestmark`."""
    aliases = _aliases(tree)
    pending: list[tuple[ast.AST, frozenset[str]]] = [(tree, frozenset(_own_marks(tree.body, aliases)))]
    while pending:
        node, inherited = pending.pop()
        for child in ast.iter_child_nodes(node):
            marks = inherited
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                marks = inherited.union(*(_marks_named(decorator, aliases) for decorator in child.decorator_list))
            if isinstance(child, ast.ClassDef):
                marks = marks.union(_own_marks(child.body, aliases))
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


def imports_adapter_line(source: str | bytes) -> int | None:
    """The line of the module's `pytestmark` that names the `imports_adapter` mark; a module that does not parse has none."""
    if (IMPORTS_ADAPTER if isinstance(source, str) else IMPORTS_ADAPTER.encode()) not in source:
        return None
    try:
        return module_marks(ast.parse(source)).get(IMPORTS_ADAPTER)
    except (SyntaxError, ValueError):
        return None
