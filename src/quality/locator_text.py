"""The `locator-text` rule: a covered repository's Python test that finds an element by a catalog text is a finding, one per
site. A test finds it by its key through the generated module. A test file Python cannot read or a catalog design's strict
reading refuses refuses the rule, because the sites after it would be missed in silence."""

import ast
import re
from collections import Counter
from collections.abc import Iterator
from pathlib import Path, PurePosixPath

from design.catalogs import CatalogEntry, CatalogFileError, parse_catalog

from quality import catalog_rules, checkout, repository
from quality.catalog_rules import Catalogs
from quality.report import Finding, FindingEvent, RuleResult, Unparsable
from quality.rules import Rule

PYTHON_SUFFIX = ".py"
TEST_DIRECTORY = "tests"
LOCATOR_CALL = re.compile(r"getBy[A-Z]\w*|get_by_[a-z]\w*")
ASSERTION_CALLS = frozenset(
    {"to_have_text", "toHaveText", "to_have_accessible_name", "toHaveAccessibleName", "to_have_title", "toHaveTitle"}
)
ATTRIBUTE_CALLS = frozenset({"to_have_attribute", "toHaveAttribute"})
ATTRIBUTE_VALUE_KEYWORD = "value"
FILTER_KEYWORDS = frozenset({"has_text", "hasText"})
ANCHORED_REGEX = re.compile(r"\^(.+)\$", re.DOTALL)
REGEX_ESCAPE = re.compile(r"\\(\W)")
REGEX_COMPILE_CALL = "compile"
FIX = "find it by its key through the generated module"

SCOPE_DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
SCOPES = (*SCOPE_DEFINITIONS, ast.Lambda)

type Value = ast.Constant | ast.Name
type Site = tuple[Value, bool]
type CatalogTexts = dict[str, list[str]]


def _surface_texts(entry: CatalogEntry) -> list[str]:
    """The texts a person can read from an entry: its msgid, and its non-empty msgstr forms unless it is fuzzy, which gettext
    does not show."""
    translations = [] if entry.fuzzy else entry.msgstr
    return [text for text in (entry.msgid, *translations) if text]


def _catalog_texts(root: Path, tracked: list[str], directory: PurePosixPath) -> CatalogTexts:
    """Each text of the tracked catalogs in the directory with the keys that hold it, sorted; a key is the msgctxt."""
    names = checkout.on_disk(root, catalog_rules.tracked_catalogs(tracked, directory))
    keys: dict[str, set[str]] = {}
    for name, content in checkout.read_existing(repository.read_bytes, root, names).items():
        try:
            parsed = parse_catalog(content, name)
        except CatalogFileError as error:
            raise Unparsable(str(error)) from error
        for entry in parsed.entries:
            for text in _surface_texts(entry):
                keys.setdefault(text, set()).add(entry.msgctxt or entry.msgid)
    return {text: sorted(held) for text, held in keys.items()}


def _is_test_file(name: str) -> bool:
    path = PurePosixPath(name)
    return path.suffix == PYTHON_SUFFIX and TEST_DIRECTORY in path.parent.parts


def _module_level_nodes(node: ast.AST) -> Iterator[ast.AST]:
    """Every node below `node` that binds in the module's own scope: not the bodies of functions, classes and lambdas."""
    for child in ast.iter_child_nodes(node):
        yield child
        if not isinstance(child, SCOPES):
            yield from _module_level_nodes(child)


def _bound_names(node: ast.AST) -> list[str]:
    """The names a node binds: a store, an import, a function or class definition, an `except ... as` handler."""
    if isinstance(node, ast.Name):
        return [] if isinstance(node.ctx, ast.Load) else [node.id]
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return [(alias.asname or alias.name).split(".")[0] for alias in node.names]
    if isinstance(node, (*SCOPE_DEFINITIONS, ast.ExceptHandler)) and node.name:
        return [node.name]
    return []


def _plain_assignments(statement: ast.stmt) -> list[tuple[str, ast.expr]]:
    """The (name, value) pairs of a module-level statement that assigns a value straight to a name."""
    if isinstance(statement, ast.Assign):
        return [(target.id, statement.value) for target in statement.targets if isinstance(target, ast.Name)]
    if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name) and statement.value:
        return [(statement.target.id, statement.value)]
    return []


def _module_constants(tree: ast.Module) -> dict[str, str]:
    """The names bound exactly once in the module's own scope, by an assignment of a plain string literal."""
    bindings = Counter(name for node in _module_level_nodes(tree) for name in _bound_names(node))
    return {
        name: value.value
        for statement in tree.body
        for name, value in _plain_assignments(statement)
        if bindings[name] == 1 and isinstance(value, ast.Constant) and isinstance(value.value, str)
    }


def _called_name(call: ast.Call) -> str:
    function = call.func
    if isinstance(function, ast.Attribute):
        return function.attr
    return function.id if isinstance(function, ast.Name) else ""


def _argument_expressions(call: ast.Call) -> list[ast.expr]:
    return [*call.args, *(keyword.value for keyword in call.keywords)]


def _attribute_value(call: ast.Call) -> list[ast.expr]:
    """The value argument of a `to_have_attribute` call: the second positional one, or the `value` keyword."""
    keywords = [keyword.value for keyword in call.keywords if keyword.arg == ATTRIBUTE_VALUE_KEYWORD]
    return [*call.args[1:2], *keywords]


def _locator_arguments(call: ast.Call) -> list[ast.expr]:
    """The argument expressions of a call that sit in a locator position."""
    name = _called_name(call)
    positions = _attribute_value(call) if name in ATTRIBUTE_CALLS else []
    if LOCATOR_CALL.fullmatch(name) or name in ASSERTION_CALLS:
        positions += _argument_expressions(call)
    return positions + [keyword.value for keyword in call.keywords if keyword.arg in FILTER_KEYWORDS]


def _values(expression: ast.expr, in_regex: bool = False) -> Iterator[Site]:
    """The names and constants an argument is made of, each with whether a regex compile holds it: itself, the arguments of a
    helper call it holds, the elements of a list."""
    if isinstance(expression, (ast.Constant, ast.Name)):
        yield expression, in_regex
    elif isinstance(expression, ast.Call):
        inside = in_regex or _called_name(expression) == REGEX_COMPILE_CALL
        for argument in _argument_expressions(expression):
            yield from _values(argument, inside)
    elif isinstance(expression, (ast.List, ast.Tuple, ast.Set)):
        for element in expression.elts:
            yield from _values(element, in_regex)


def _sites(tree: ast.Module) -> list[Site]:
    """Each value in a locator position, once, in source order."""
    found = {
        (value.lineno, value.col_offset): (value, in_regex)
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        for argument in _locator_arguments(call)
        for value, in_regex in _values(argument)
    }
    return [found[position] for position in sorted(found)]


def _literal(value: Value, constants: dict[str, str]) -> str | None:
    if isinstance(value, ast.Name):
        return constants.get(value.id)
    return value.value if isinstance(value.value, str) else None


def _catalog_text(literal: str, texts: CatalogTexts, in_regex: bool) -> str | None:
    """The catalog text the literal is whole: itself unless a regex compile holds it, or the body of an anchored `^...$` regex."""
    if literal in texts and not in_regex:
        return literal
    anchored = ANCHORED_REGEX.fullmatch(literal)
    body = REGEX_ESCAPE.sub(r"\1", anchored.group(1)) if anchored else None
    return body if body in texts else None


def _fact(text: str, keys: list[str]) -> str:
    first, *rest = keys
    more = f", and {len(rest)} more keys" if rest else ""
    return f"'{text}' is the catalog text of {first}{more}"


def _file_findings(name: str, content: bytes, texts: CatalogTexts) -> list[Finding]:
    try:
        tree = ast.parse(content, name)
    except (SyntaxError, ValueError, RecursionError) as error:
        raise Unparsable(f"{name} cannot be read as Python ({error})") from error
    constants = _module_constants(tree)
    findings = []
    for value, in_regex in _sites(tree):
        literal = _literal(value, constants)
        text = None if literal is None else _catalog_text(literal, texts, in_regex)
        if text is not None:
            fact = _fact(text, texts[text])
            findings.append(Finding(Rule.LOCATOR_TEXT, name, value.lineno, fact, FIX, event=FindingEvent.CATALOG_TEXT))
    return findings


def check_locator_text(root: Path, tracked: list[str], catalogs: Catalogs) -> RuleResult:
    """One finding per site of a catalog text in a locator position of the tracked Python test files, at its line; the extent
    is the test files read. A covered repository is one `catalog_rules.read_catalogs` returned."""
    texts = _catalog_texts(root, tracked, catalog_rules.catalogs_directory(catalogs))
    files = checkout.read_tracked(repository.read_bytes, root, tracked, _is_test_file)
    findings = [finding for name, content in files.items() for finding in _file_findings(name, content, texts)]
    return RuleResult(findings, len(files), False)
