"""A repository's tests: how many there are in each layer, how many carry a skip or a fixme mark, and the script test call
that `spec-citation` reads. The layer comes from the path and the `integration` mark, the marks from the syntax (a Python
test's from `pytest_marks`); no repository code runs, so a mark that only a run applies is not seen."""

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import PurePosixPath

from quality import clones, layout, pytest_marks
from quality.ratchet import language_of
from quality.typescript_tokens import Kind, ScriptSyntaxError, Token, tokenize

SCRIPT_TEST_CALLS = frozenset({"it", "test"})
SCRIPT_TEST = "test"
SCRIPT_GROUP = "describe"
SCRIPT_MODIFIERS = frozenset({"skip", "skipIf", "runIf", "only", "fixme", "concurrent", "each"})
SCRIPT_CALLED_MODIFIERS = frozenset({"skipIf", "runIf", "each"})
SCRIPT_RUNTIME_MARKS = frozenset({"skip", "fixme"})


class Layer(Enum):
    UNIT = "Unit"
    INTEGRATION = "Integration"
    FLOW = "Flow"
    LIVE = "Live"


class Mark(Enum):
    SKIPPED = auto()
    FIXME = auto()
    INTEGRATION = auto()


PYTEST_MARKS = {"skip": Mark.SKIPPED, "skipif": Mark.SKIPPED, "xfail": Mark.FIXME, "integration": Mark.INTEGRATION}
SCRIPT_MARKS = {"skip": Mark.SKIPPED, "skipIf": Mark.SKIPPED, "fixme": Mark.FIXME}
Marks = frozenset[Mark]
NO_MARKS: Marks = frozenset()


@dataclass(frozen=True)
class Suites:
    """The tests of a repository by layer, those a skip mark reaches, those a fixme mark reaches (one test can be both),
    and the test files that were not read."""

    layers: dict[Layer, int]
    skipped: int
    fixme: int
    unread: int


def script_titles(tokens: list[Token]) -> Iterator[Token]:
    """The title of each bare `it(` or `test(` call whose first argument is a string or a template without a hole."""
    for before, name, opening, title in zip([None, *tokens], tokens, tokens[1:], tokens[2:], strict=False):
        is_bare = before is None or before.value != "."
        if is_bare and name.kind is Kind.IDENTIFIER and name.value in SCRIPT_TEST_CALLS and opening.value == "(" and title.kind is Kind.STRING:
            yield title


def is_script_test(path: str) -> bool:
    language = language_of(path)
    return language in layout.SCRIPT_LANGUAGES and language.is_test_file(PurePosixPath(path))


def read_script_tests(tracked: list[str], sources: dict[str, bytes]) -> tuple[dict[str, list[Token]], list[str]]:
    """The tokens of each tracked TypeScript and JavaScript test file, and the files that are absent from the tree,
    not UTF-8 or refused by the lexer."""
    read: dict[str, list[Token]] = {}
    unread: list[str] = []
    for path in (path for path in tracked if is_script_test(path)):
        if path not in sources:
            unread.append(path)
            continue
        try:
            read[path] = tokenize(sources[path].decode("utf-8"))
        except (UnicodeDecodeError, ScriptSyntaxError):
            unread.append(path)
    return read, unread


def _python_test_marks(tree: ast.Module) -> Iterator[Marks]:
    """The marks that reach each test function, among those that count."""
    return (frozenset(PYTEST_MARKS[name] for name in names if name in PYTEST_MARKS) for names in pytest_marks.marks_by_test(tree))


@dataclass(frozen=True)
class _ScriptCall:
    """A call of a test or a group, or a runtime `test.skip(<condition>)`, and the token range of its arguments."""

    name: str
    marks: Marks
    start: int
    end: int


def _paired_parentheses(tokens: list[Token]) -> dict[int, int]:
    opened: list[int] = []
    paired: dict[int, int] = {}
    for index, token in enumerate(tokens):
        if token.kind is Kind.PUNCTUATION and token.value == "(":
            opened.append(index)
        elif token.kind is Kind.PUNCTUATION and token.value == ")" and opened:
            paired[opened.pop()] = index
    return paired


def _value_at(tokens: list[Token], index: int) -> str:
    return tokens[index].value if 0 <= index < len(tokens) else ""


def _chain(tokens: list[Token], start: int, paired: dict[int, int]) -> tuple[list[str], int] | None:
    """The names of `a.b(c).d` read from `start` and the index of the `(` that calls them, None where no call follows."""
    names = [tokens[start].value]
    position = start + 1
    while _value_at(tokens, position) == "." and position + 1 < len(tokens) and tokens[position + 1].kind is Kind.IDENTIFIER:
        names.append(tokens[position + 1].value)
        position += 2
        if names[-1] in SCRIPT_CALLED_MODIFIERS:
            if position not in paired:
                return None
            position = paired[position] + 1
    return (names, position) if _value_at(tokens, position) == "(" else None


def _script_call(tokens: list[Token], start: int, paired: dict[int, int]) -> _ScriptCall | None:
    chained = _chain(tokens, start, paired)
    if chained is None:
        return None
    (base, *rest), opening = chained
    is_group = base == SCRIPT_GROUP or (base == SCRIPT_TEST and rest[:1] == [SCRIPT_GROUP])
    modifiers = rest[1:] if base == SCRIPT_TEST and is_group else rest
    if not set(modifiers) <= SCRIPT_MODIFIERS:
        return None
    marks = frozenset(SCRIPT_MARKS[modifier] for modifier in modifiers if modifier in SCRIPT_MARKS)
    end = paired.get(opening, len(tokens))
    titled = opening + 1 < len(tokens) and tokens[opening + 1].kind in {Kind.STRING, Kind.TEMPLATE}
    if titled:
        return _ScriptCall(SCRIPT_GROUP if is_group else SCRIPT_TEST, marks, opening, end)
    if base == SCRIPT_TEST and len(rest) == 1 and rest[0] in SCRIPT_RUNTIME_MARKS:
        return _ScriptCall(rest[0], marks, opening, end)
    return None


def _call_nesting(calls: list[_ScriptCall]) -> tuple[dict[int, int | None], dict[int, set[Mark]], set[Mark]]:
    """The enclosing call of each test or group call, the marks of the runtime skip or fixme calls each one holds, and
    those of the runtime calls outside every test and group, which reach every test of the file."""
    holders: list[int] = []
    parent: dict[int, int | None] = {}
    runtime: dict[int, set[Mark]] = {}
    file_level: set[Mark] = set()
    for index, call in enumerate(calls):
        while holders and calls[holders[-1]].end < call.start:
            holders.pop()
        if call.name in {SCRIPT_TEST, SCRIPT_GROUP}:
            parent[index] = holders[-1] if holders else None
            holders.append(index)
        elif holders:
            runtime.setdefault(holders[-1], set()).update(call.marks)
        else:
            file_level |= call.marks
    return parent, runtime, file_level


def _script_test_marks(tokens: list[Token]) -> Iterator[Marks]:
    """The marks that reach each test call: its own, those of every enclosing group, and a runtime skip or fixme call, which
    marks the innermost test or group that holds it, or every test of the file where none holds it."""
    paired = _paired_parentheses(tokens)
    calls = [
        call
        for index, token in enumerate(tokens)
        if token.kind is Kind.IDENTIFIER and token.value in {*SCRIPT_TEST_CALLS, SCRIPT_GROUP} and _value_at(tokens, index - 1) != "."
        if (call := _script_call(tokens, index, paired))
    ]
    parent, runtime, file_level = _call_nesting(calls)
    for index, call in enumerate(calls):
        if call.name == SCRIPT_TEST:
            marks, holder = file_level | call.marks | runtime.get(index, set()), parent[index]
            while holder is not None:
                marks |= calls[holder].marks | runtime.get(holder, set())
                holder = parent[holder]
            yield frozenset(marks)


def _live_and_flow_folders(tracked: list[str]) -> tuple[list[PurePosixPath], list[PurePosixPath]]:
    """The folders of each judged project's `tests/` and of each frontend project, that name the live and the flow layer."""
    roots = [PurePosixPath(layout.TESTS_DIRECTORY), *(project.tests_directory for project in layout.python_projects(tracked))]
    roots += [PurePosixPath(directory) for directory in layout.frontend_projects(tracked)]
    return [root / layout.LIVE_FOLDER for root in roots], [root / layout.END_TO_END_DIRECTORY for root in roots]


def count_suites(tracked: list[str], sources: dict[str, bytes]) -> Suites:
    """The tests of the tracked files among `sources`; a file that cannot be read adds to `unread` and no test."""
    live, flow = _live_and_flow_folders(tracked)

    def layer_of(path: str, marks: Marks) -> Layer:
        parents = PurePosixPath(path).parents
        if any(folder in parents for folder in live):
            return Layer.LIVE
        if any(folder in parents for folder in flow):
            return Layer.FLOW
        return Layer.INTEGRATION if Mark.INTEGRATION in marks else Layer.UNIT

    trees = dict(clones.python_test_trees(sources))
    scripts, unread_scripts = read_script_tests(tracked, sources)
    unread = [path for path in sources if clones.is_python_test(path) and path not in trees] + unread_scripts
    marked = [(path, _python_test_marks(tree)) for path, tree in trees.items()] + [(path, _script_test_marks(tokens)) for path, tokens in scripts.items()]
    layers = dict.fromkeys(Layer, 0)
    skipped = fixme = 0
    for path, tests in marked:
        for marks in tests:
            layers[layer_of(path, marks)] += 1
            skipped += Mark.SKIPPED in marks
            fixme += Mark.FIXME in marks
    return Suites(layers, skipped, fixme, len(unread))
