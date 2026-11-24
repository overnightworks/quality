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


def _stored_names(node: ast.AST) -> Iterator[str]:
    """The names a target, pattern or `del` binds or removes, in any nesting of tuples and stars."""
    for part in ast.walk(node):
        if isinstance(part, ast.Name) and not isinstance(part.ctx, ast.Load):
            yield part.id
        elif isinstance(part, ast.MatchAs | ast.MatchStar) and part.name is not None:
            yield part.name
        elif isinstance(part, ast.MatchMapping) and part.rest is not None:
            yield part.rest


def _unreadable_names(node: ast.AST) -> Iterator[str]:
    """The names a node binds in a form whose value this reader cannot follow: a tuple, star, loop, `with`, `except`,
    import, definition, `del` or match target."""
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if not isinstance(target, ast.Name):
                yield from _stored_names(target)
    elif isinstance(node, ast.For | ast.AsyncFor):
        yield from _stored_names(node.target)
    elif isinstance(node, ast.withitem) and node.optional_vars is not None:
        yield from _stored_names(node.optional_vars)
    elif isinstance(node, ast.Import | ast.ImportFrom):
        for alias in node.names:
            yield alias.asname or alias.name.partition(".")[0]
    elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.ExceptHandler) and node.name is not None:
        yield node.name
    elif isinstance(node, ast.Delete):
        for target in node.targets:
            yield from _stored_names(target)
    elif isinstance(node, ast.pattern):
        yield from _stored_names(node)


def _bindings(scope: ast.AST) -> Iterator[tuple[str, ast.expr | None, int]]:
    """Each binding of a name in the scope's own statements, also inside compound statements, in source order: its name,
    its value (none where this reader cannot follow it: an augmented binding or a tuple, loop, import, definition or
    other target) and its line. Functions and classes open their own scope."""
    for child in ast.iter_child_nodes(scope):
        line = getattr(child, "lineno", getattr(scope, "lineno", 0))
        if isinstance(child, ast.expr):
            yield from ((walrus.target.id, walrus.value, walrus.lineno) for walrus in ast.walk(child) if isinstance(walrus, ast.NamedExpr))
            continue
        if isinstance(child, ast.Assign):
            yield from ((target.id, child.value, line) for target in child.targets if isinstance(target, ast.Name))
        elif isinstance(child, ast.AnnAssign) and child.value is not None and isinstance(child.target, ast.Name):
            yield child.target.id, child.value, line
        elif isinstance(child, ast.AugAssign) and isinstance(child.target, ast.Name):
            yield child.target.id, None, line
        yield from ((name, None, line) for name in _unreadable_names(child))
        if not isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            yield from _bindings(child)


def _aliases(tree: ast.Module) -> dict[str, frozenset[str]]:
    """The marks each module-level name holds by its last binding; a name rebound to no mark holds none."""
    last = {name: NO_MARKS if value is None else _marks_named(value, {}) for name, value, _ in _bindings(tree)}
    return {name: marks for name, marks in last.items() if marks and name != MODULE_MARKS}


def _last_pytestmark(scope: ast.AST, aliases: dict[str, frozenset[str]]) -> tuple[frozenset[str], int]:
    """The marks of the scope's last `pytestmark` binding, which is the one pytest applies (`get_unpacked_marks` reads
    the attribute the body leaves), and its line; no binding, an augmented one or one that is no mark expression has none."""
    marks, line = NO_MARKS, 0
    for name, value, bound_line in _bindings(scope):
        if name == MODULE_MARKS:
            marks, line = (NO_MARKS if value is None else _marks_named(value, aliases)), bound_line
    return marks, line


def _module_marks(tree: ast.Module, aliases: dict[str, frozenset[str]]) -> dict[str, int]:
    marks, line = _last_pytestmark(tree, aliases)
    return dict.fromkeys(sorted(marks), line)


def module_marks(tree: ast.Module) -> dict[str, int]:
    """Each mark the module's last `pytestmark` binding names, with the line of that binding."""
    return _module_marks(tree, _aliases(tree))


def marks_by_test(tree: ast.Module) -> Iterator[frozenset[str]]:
    """The marks that reach each test function: its own, its enclosing classes' and the module's `pytestmark`."""
    aliases = _aliases(tree)
    pending: list[tuple[ast.AST, frozenset[str]]] = [(tree, frozenset(_module_marks(tree, aliases)))]
    while pending:
        node, inherited = pending.pop()
        for child in ast.iter_child_nodes(node):
            marks = inherited
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                marks = inherited.union(*(_marks_named(decorator, aliases) for decorator in child.decorator_list))
            if isinstance(child, ast.ClassDef):
                marks = marks.union(_last_pytestmark(child, aliases)[0])
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
