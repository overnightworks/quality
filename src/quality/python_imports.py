"""The one reader of the import statements of a Python module, as Python's own parser reads them."""

import ast
from dataclasses import dataclass

from quality import ratchet


@dataclass(frozen=True)
class Import:
    """`import a.b, c`: the dotted names it imports."""

    line: int
    names: tuple[str, ...]


@dataclass(frozen=True)
class FromImport:
    """`from ..m import x, *`: the number of leading dots, the module (empty for `from . import x`) and the imported
    names, `*` included."""

    line: int
    level: int
    module: str
    names: tuple[str, ...]


Statement = Import | FromImport


def import_statements(path: str, content: bytes) -> list[Statement]:
    """Every `import` and `from ... import` statement of the module, in a function or under `if TYPE_CHECKING:` too.

    A module Python cannot parse raises one of `ratchet.PARSE_ERRORS` as it came, for the caller to word."""
    tree = ast.parse(ratchet.python_text(content), filename=path)
    return [_statement(node) for node in ast.walk(tree) if isinstance(node, ast.Import | ast.ImportFrom)]


def _statement(node: ast.Import | ast.ImportFrom) -> Statement:
    names = tuple(alias.name for alias in node.names)
    if isinstance(node, ast.Import):
        return Import(node.lineno, names)
    return FromImport(node.lineno, node.level, node.module or "", names)
