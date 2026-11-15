"""The `locator-text` rule: a covered repository's Python, TypeScript or JavaScript test that finds an element by a catalog
text is a finding, one per site. A test finds it by its key through the generated module. A test file that cannot be read or
a catalog design's strict reading refuses refuses the rule, because the sites after it would be missed in silence."""

import ast
import fnmatch
import re
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from design.catalogs import CatalogEntry, CatalogFileError, parse_catalog

from quality import catalog_rules, checkout, repository
from quality.catalog_rules import Catalogs
from quality.literal_text import READ_ATTRIBUTES
from quality.report import Finding, FindingEvent, RuleResult, Unparsable
from quality.rules import Rule
from quality.typescript_tokens import Kind, ScriptSyntaxError, Token, tokenize

PYTHON_SUFFIX = ".py"
SCRIPT_SUFFIXES = frozenset({".ts", ".js"})
TEST_DIRECTORY = "tests"
SCRIPT_TEST_DIRECTORIES = frozenset({"e2e", "test-utils"})
SCRIPT_TEST_PATTERNS = ("*.test.*", "*.spec.*")
LOCATOR_CALL = re.compile(r"getBy[A-Z]\w*|get_by_[a-z]\w*")
ASSERTION_CALLS = frozenset(
    {
        "to_have_text", "toHaveText", "to_have_accessible_name", "toHaveAccessibleName", "to_have_title", "toHaveTitle",
        "to_have_accessible_description", "toHaveAccessibleDescription",
    }
)
ATTRIBUTE_CALLS = frozenset({"to_have_attribute", "toHaveAttribute"})
ATTRIBUTE_NAME_KEYWORD = "name"
ATTRIBUTE_VALUE_KEYWORD = "value"
FILTER_KEYWORDS = frozenset({"has_text", "hasText", "has_not_text", "hasNotText"})
ROLE_CALLS = frozenset({"get_by_role", "getByRole"})
ROLE_KEYWORD = "role"
TEST_ID_CALLS = frozenset({"get_by_test_id", "getByTestId"})
ANCHORED_REGEX = re.compile(r"\^(.+)\$", re.DOTALL)
REGEX_ESCAPE = re.compile(r"\\(\W)")
REGEX_COMPILE_CALL = "compile"
REGEX_CONSTRUCTOR = "RegExp"
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


def _is_python_test(path: PurePosixPath) -> bool:
    return path.suffix == PYTHON_SUFFIX and TEST_DIRECTORY in path.parent.parts


def _is_script_test(path: PurePosixPath) -> bool:
    """A TypeScript or JavaScript file named `*.test.*` or `*.spec.*`, or under a directory of end-to-end tests or helpers."""
    marked = any(fnmatch.fnmatchcase(path.name, pattern) for pattern in SCRIPT_TEST_PATTERNS)
    return path.suffix in SCRIPT_SUFFIXES and (marked or not SCRIPT_TEST_DIRECTORIES.isdisjoint(path.parent.parts))


def _is_test_file(name: str) -> bool:
    path = PurePosixPath(name)
    return _is_python_test(path) or _is_script_test(path)


def _module_level_nodes(node: ast.AST) -> Iterator[ast.AST]:
    """Every node below `node` that binds in the module's own scope: not the bodies of functions, classes and lambdas."""
    pending = list(ast.iter_child_nodes(node))
    while pending:
        child = pending.pop()
        yield child
        if not isinstance(child, SCOPES):
            pending.extend(ast.iter_child_nodes(child))


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
    """The value argument of a `to_have_attribute` call: the second positional one, or the `value` keyword; none unless the
    attribute is a literal one a person reads."""
    names = [*call.args[:1], *(keyword.value for keyword in call.keywords if keyword.arg == ATTRIBUTE_NAME_KEYWORD)]
    if not any(isinstance(name, ast.Constant) and name.value in READ_ATTRIBUTES for name in names):
        return []
    return [*call.args[1:2], *(keyword.value for keyword in call.keywords if keyword.arg == ATTRIBUTE_VALUE_KEYWORD)]


def _surface_arguments(call: ast.Call, name: str) -> list[ast.expr]:
    """The arguments of a call that carry surface text: the ARIA role of `get_by_role` and a test id are protocol tokens."""
    if name in TEST_ID_CALLS:
        return []
    if name in ROLE_CALLS:
        return [*call.args[1:], *(keyword.value for keyword in call.keywords if keyword.arg != ROLE_KEYWORD)]
    return _argument_expressions(call)


def _locator_arguments(call: ast.Call) -> list[ast.expr]:
    """The argument expressions of a call that sit in a locator position."""
    name = _called_name(call)
    positions = _attribute_value(call) if name in ATTRIBUTE_CALLS else []
    if LOCATOR_CALL.fullmatch(name) or name in ASSERTION_CALLS:
        positions += _surface_arguments(call, name)
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


@dataclass(frozen=True)
class Reading:
    """A value in a locator position: its line, its literal text (None where a name or call has none) and whether a regex
    holds it."""

    line: int
    literal: str | None
    in_regex: bool


def _python_literal(value: Value, constants: dict[str, str]) -> str | None:
    if isinstance(value, ast.Name):
        return constants.get(value.id)
    return value.value if isinstance(value.value, str) else None


def _python_readings(name: str, content: bytes) -> list[Reading]:
    try:
        tree = ast.parse(content, name)
    except (SyntaxError, ValueError, RecursionError) as error:
        raise Unparsable(f"{name} cannot be read as Python ({error})") from error
    constants = _module_constants(tree)
    return [Reading(value.lineno, _python_literal(value, constants), in_regex) for value, in_regex in _sites(tree)]


OPENERS = ("(", "[", "{")
CLOSERS = (")", "]", "}")
DECLARATIONS = frozenset({"const", "let", "var", "function", "class"})
COMPOUND_OPERATORS = ("+", "-", "*", "/", "%", "&", "|", "^")
SITE_KINDS = frozenset({Kind.STRING, Kind.REGEX, Kind.IDENTIFIER})


def _is_punctuation(token: Token | None, *values: str) -> bool:
    return token is not None and token.kind is Kind.PUNCTUATION and token.value in values


def _at(tokens: Sequence[Token], index: int) -> Token | None:
    return tokens[index] if 0 <= index < len(tokens) else None


def _extent(tokens: Sequence[Token], start: int) -> int:
    """The index where the expression starting at `start` ends: a top-level comma, a closer nothing here opened, or the end."""
    depth = 0
    for index in range(start, len(tokens)):
        if _is_punctuation(tokens[index], *OPENERS):
            depth += 1
        elif _is_punctuation(tokens[index], *CLOSERS):
            if depth == 0:
                return index
            depth -= 1
        elif depth == 0 and _is_punctuation(tokens[index], ","):
            return index
    return len(tokens)


def _arguments(tokens: Sequence[Token], opening: int) -> list[range]:
    """The token ranges of the comma-separated arguments of the call whose `(` is at `opening`; a call never closed runs to
    the end."""
    arguments = []
    start = opening + 1
    while True:
        end = _extent(tokens, start)
        arguments.append(range(start, end))
        if not _is_punctuation(_at(tokens, end), ","):
            return arguments
        start = end + 1


def _declarations(tokens: Sequence[Token]) -> Iterator[int]:
    """The index of each declaring keyword (`const`, `let`, `var`, `function`, `class`) that stands at the top level."""
    depth = 0
    for index, token in enumerate(tokens):
        if _is_punctuation(token, *CLOSERS):
            depth = max(depth - 1, 0)
        elif depth == 0 and token.kind is Kind.IDENTIFIER and token.value in DECLARATIONS:
            yield index
        if _is_punctuation(token, *OPENERS):
            depth += 1


def _ends_declaration(tokens: Sequence[Token], index: int) -> bool:
    """Whether the statement whose value is at `index` ends there: at `;`, at `as const`, at the end of the file, or at a line
    that starts with an identifier."""
    following = _at(tokens, index + 1)
    if following is None or _is_punctuation(following, ";"):
        return True
    if following.kind is not Kind.IDENTIFIER:
        return False
    after = _at(tokens, index + 2)
    return following.line > tokens[index].line or (following.value == "as" and after is not None and after.value == "const")


def _constant_value(tokens: Sequence[Token], index: int) -> str | None:
    """The string the `const` at `index` is set to, when its statement ends right after it."""
    assignment, value = _at(tokens, index + 2), _at(tokens, index + 3)
    if tokens[index].value != "const" or not _is_punctuation(assignment, "=") or value is None or value.kind is not Kind.STRING:
        return None
    return value.value if _ends_declaration(tokens, index + 3) else None


def _is_assignment(tokens: Sequence[Token], index: int) -> bool:
    """Whether the name at `index` is followed by `=` or a compound `+=`, `-=`, ... but not by `==`, `===` or `=>`."""
    operator = index + 2 if _is_punctuation(_at(tokens, index + 1), *COMPOUND_OPERATORS) else index + 1
    return _is_punctuation(_at(tokens, operator), "=") and not _is_punctuation(_at(tokens, operator + 1), "=", ">")


def _reassigned(tokens: Sequence[Token]) -> set[str]:
    """The names an assignment statement changes, anywhere in the file: a name right after a declaring keyword or a `.` is
    a declaration or a property, not a reassignment."""
    names: set[str] = set()
    for index, token in enumerate(tokens):
        previous = _at(tokens, index - 1)
        declaring = previous is not None and previous.kind is Kind.IDENTIFIER and previous.value in DECLARATIONS
        if token.kind is Kind.IDENTIFIER and _is_assignment(tokens, index) and not declaring and not _is_punctuation(previous, "."):
            names.add(token.value)
    return names


def _script_constants(tokens: Sequence[Token]) -> dict[str, str]:
    """The names declared exactly once at the top level, by a `const` set to a plain string or a template without a hole, and
    never assigned to."""
    declared: Counter[str] = Counter()
    strings: dict[str, str] = {}
    for index in _declarations(tokens):
        name = _at(tokens, index + 1)
        if name is None or name.kind is not Kind.IDENTIFIER:
            continue
        declared[name.value] += 1
        text = _constant_value(tokens, index)
        if text is not None:
            strings[name.value] = text
    reassigned = _reassigned(tokens)
    return {name: text for name, text in strings.items() if declared[name] == 1 and name not in reassigned}


def _is_read_attribute(tokens: Sequence[Token], argument: range) -> bool:
    """Whether an argument is one string literal naming an attribute a person reads."""
    return len(argument) == 1 and tokens[argument[0]].kind is Kind.STRING and tokens[argument[0]].value in READ_ATTRIBUTES


def _call_positions(tokens: Sequence[Token], index: int) -> list[range]:
    """The argument ranges of the call named at `index` that sit in a locator position."""
    name = tokens[index].value
    if not _is_punctuation(_at(tokens, index + 1), "("):
        return []
    arguments = _arguments(tokens, index + 1)
    if name in ATTRIBUTE_CALLS:
        return arguments[1:2] if _is_read_attribute(tokens, arguments[0]) else []
    if name in TEST_ID_CALLS or not (LOCATOR_CALL.fullmatch(name) or name in ASSERTION_CALLS):
        return []
    return arguments[1:] if name in ROLE_CALLS else arguments


def _positions(tokens: Sequence[Token]) -> set[int]:
    """The indices of the tokens inside a locator position: call arguments and the value of a `hasText:` property."""
    positions: set[int] = set()
    for index, token in enumerate(tokens):
        if token.kind is not Kind.IDENTIFIER:
            continue
        ranges = _call_positions(tokens, index)
        if token.value in FILTER_KEYWORDS and _is_punctuation(_at(tokens, index + 1), ":"):
            ranges.append(range(index + 2, _extent(tokens, index + 2)))
        for span in ranges:
            positions.update(span)
    return positions


def _opens_position(tokens: Sequence[Token], index: int) -> bool:
    """Whether the token follows `(`, `,`, the `[` of an array (never an index) or a `key:` of an object."""
    previous = _at(tokens, index - 1)
    if _is_punctuation(previous, "(", ","):
        return True
    before = _at(tokens, index - 2)
    if _is_punctuation(previous, "["):
        indexes = before is not None and (before.kind is Kind.IDENTIFIER or _is_punctuation(before, ")", "]"))
        return not indexes
    key_start = _at(tokens, index - 3)
    is_key = before is not None and before.kind in {Kind.IDENTIFIER, Kind.STRING}
    return _is_punctuation(previous, ":") and is_key and _is_punctuation(key_start, "{", ",")


def _stands_alone(tokens: Sequence[Token], index: int) -> bool:
    return tokens[index].kind in SITE_KINDS and _is_punctuation(_at(tokens, index + 1), ")", ",", "]", "}") and _opens_position(tokens, index)


def _script_reading(tokens: Sequence[Token], index: int, constants: dict[str, str]) -> Reading:
    token = tokens[index]
    constructor = _at(tokens, index - 2)
    constructed = _is_punctuation(_at(tokens, index - 1), "(") and constructor is not None and constructor.value == REGEX_CONSTRUCTOR
    in_regex = token.kind is Kind.REGEX or constructed
    literal = constants.get(token.value) if token.kind is Kind.IDENTIFIER else token.value
    return Reading(token.line, literal, in_regex)


def _script_readings(name: str, content: bytes) -> list[Reading]:
    try:
        tokens = tokenize(content.decode("utf-8"))
    except (UnicodeDecodeError, ScriptSyntaxError) as error:
        raise Unparsable(f"{name} cannot be read as TypeScript or JavaScript ({error})") from error
    constants = _script_constants(tokens)
    return [_script_reading(tokens, index, constants) for index in sorted(_positions(tokens)) if _stands_alone(tokens, index)]


def _catalog_text(literal: str, texts: CatalogTexts, in_regex: bool) -> str | None:
    """The catalog text the literal is whole: itself, or when a regex holds it the body of an anchored `^...$` regex."""
    if not in_regex:
        return literal if literal in texts else None
    anchored = ANCHORED_REGEX.fullmatch(literal)
    body = REGEX_ESCAPE.sub(r"\1", anchored.group(1)) if anchored else None
    return body if body in texts else None


def _fact(text: str, keys: list[str]) -> str:
    first, *rest = keys
    more = f", and {len(rest)} more keys" if rest else ""
    return f"'{text}' is the catalog text of {first}{more}"


def _file_findings(name: str, content: bytes, texts: CatalogTexts) -> list[Finding]:
    readings = _python_readings(name, content) if PurePosixPath(name).suffix == PYTHON_SUFFIX else _script_readings(name, content)
    findings = []
    for reading in readings:
        text = None if reading.literal is None else _catalog_text(reading.literal, texts, reading.in_regex)
        if text is not None:
            fact = _fact(text, texts[text])
            findings.append(Finding(Rule.LOCATOR_TEXT, name, reading.line, fact, FIX, event=FindingEvent.CATALOG_TEXT))
    return findings


def check_locator_text(root: Path, tracked: list[str], catalogs: Catalogs) -> RuleResult:
    """One finding per site of a catalog text in a locator position of the tracked Python test files, at its line; the extent
    is the test files read. A covered repository is one `catalog_rules.read_catalogs` returned."""
    texts = _catalog_texts(root, tracked, catalog_rules.catalogs_directory(catalogs))
    return checkout.judge_tracked(
        repository.read_bytes, root, tracked, _is_test_file, lambda name, content: _file_findings(name, content, texts)
    )
