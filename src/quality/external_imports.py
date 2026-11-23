"""Keeps the submodules of an external package that a contract's `ignore_imports` name, which grimp squashes.

grimp reduces an external package to its top-level name, so an import of `agents.calling` and one of `agents.turn`
are the same edge into `agents`. The tracked import statements say which submodule each edge reached; this module
puts the edges back at the longest name the contract names, and at the package for every other import.
"""

from collections.abc import Iterable, Iterator, Mapping, Sequence

import grimp
from importlinter.domain.imports import ImportExpression

from quality.python_imports import Import, Statement


def named_submodules(ignores: Iterable[ImportExpression], roots: Iterable[str]) -> dict[str, frozenset[str]]:
    """The plain dotted submodules each external package is named with on the imported side of an ignore line; a
    package that is a root package, a bare package name and a wildcard name none."""
    internal = frozenset(roots)
    named: dict[str, set[str]] = {}
    for ignore in ignores:
        name = ignore.imported.expression
        package = name.partition(".")[0]
        if "." in name and "*" not in name and package not in internal:
            named.setdefault(package, set()).add(name)
    return {package: frozenset(names) for package, names in named.items()}


def refine(graph: grimp.ImportGraph, package: str, names: frozenset[str], statements: Mapping[str, Sequence[Statement]]) -> None:
    """Replaces the squashed `package` of this graph copy by one whose imports are held at a name of `names`, or at
    the package; `statements` holds the tracked statements of every module that imports the package."""
    reported = {importer: graph.get_import_details(importer=importer, imported=package) for importer in statements}
    graph.remove_module(package)
    graph.add_module(package)
    for importer, details in reported.items():
        for detail in details:
            for held in _held_names(statements[importer], detail["line_number"], package, names):
                graph.add_import(importer=importer, imported=held, line_number=detail["line_number"],
                                 line_contents=detail["line_contents"], is_lazy=detail["is_lazy"])


def _held_names(statements: Sequence[Statement], line: int, package: str, names: frozenset[str]) -> list[str]:
    """Where every import of the package at `line` is held; the package itself when no statement there names it."""
    imported = [name for statement in statements if statement.line == line
                for name in _imported_names(statement) if name == package or name.startswith(f"{package}.")]
    return list(dict.fromkeys(_held(name, package, names) for name in imported)) or [package]


def _imported_names(statement: Statement) -> Iterator[str]:
    if isinstance(statement, Import):
        yield from statement.names
    elif statement.level == 0:
        for name in statement.names:
            yield statement.module if name == "*" else f"{statement.module}.{name}"


def _held(name: str, package: str, names: frozenset[str]) -> str:
    prefixes = [candidate for candidate in names if name == candidate or name.startswith(f"{candidate}.")]
    return max(prefixes, key=len, default=package)
