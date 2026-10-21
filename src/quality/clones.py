"""The `clone` rule: functions in two different Python files whose structure is nearly the same.

A function is its token stream: the arguments and the body without its docstring, walked in pre-order, with every
name, attribute, argument and keyword name folded to `ID` and every literal folded to its class, so a copy that only
renamed things still matches. Two functions are a pair when the Jaccard similarity of their 5-token shingles is at
least 0.9; only each function's rarest shingles are indexed, since a pair at that threshold must share one.
"""

import ast
import math
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass

from quality import ratchet
from quality.ratchet import Area, ClonePair, Place

RULE = "clone"
MIN_TOKENS = 40
SHINGLE_SIZE = 5
SIMILARITY_THRESHOLD = 0.9
ID_TOKEN = "ID"
KEYWORD_SPREAD_TOKEN = "**"
FUNCTION_NODES = ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(frozen=True)
class Unit:
    """A function long enough to be a copy, with the shingles of its token stream."""

    place: Place
    shingles: frozenset[int]


def _literal_class(value: object) -> str:
    if isinstance(value, bool):
        return "B"
    if value is None:
        return "None"
    if isinstance(value, int | float | complex):
        return "N"
    if isinstance(value, str | bytes):
        return "S"
    return type(value).__name__


def _emit(node: ast.AST, tokens: list[str]) -> None:
    if isinstance(node, ast.expr_context):
        return
    tokens.append(type(node).__name__)
    if isinstance(node, ast.Constant):
        tokens.append(_literal_class(node.value))
    elif isinstance(node, ast.Name | ast.Attribute | ast.arg):
        tokens.append(ID_TOKEN)
    elif isinstance(node, ast.keyword):
        tokens.append(ID_TOKEN if node.arg else KEYWORD_SPREAD_TOKEN)
    for child in ast.iter_child_nodes(node):
        _emit(child, tokens)


def _has_docstring(body: list[ast.stmt]) -> bool:
    return (
        bool(body)
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    )


def _tokens(function: FUNCTION_NODES) -> list[str]:
    tokens: list[str] = []
    _emit(function.args, tokens)
    for statement in function.body[1:] if _has_docstring(function.body) else function.body:
        _emit(statement, tokens)
    return tokens


def _shingles(tokens: list[str]) -> frozenset[int]:
    return frozenset(hash(tuple(tokens[start : start + SHINGLE_SIZE])) for start in range(len(tokens) - SHINGLE_SIZE + 1))


def _functions(path: str, tree: ast.Module) -> Iterator[tuple[Place, FUNCTION_NODES]]:
    """Every function in source order, named the way the size rule names it."""
    symbols = ratchet.qualified_symbols(tree)
    occurrences: Counter[str] = Counter()
    for node in sorted((node for node in ast.walk(tree) if isinstance(node, FUNCTION_NODES)), key=lambda node: node.lineno):
        qualified = symbols[node.lineno]
        occurrences[qualified] += 1
        yield Place(path, ratchet.nth(qualified, occurrences[qualified])), node


def _parsed(path: str, source: bytes) -> ast.Module | None:
    try:
        return ast.parse(ratchet.python_text(source), filename=path)
    except ratchet.PARSE_ERRORS:
        return None


def _units(sources: dict[str, bytes]) -> list[Unit]:
    units = []
    for path, source in sources.items():
        if (tree := _parsed(path, source)) is None:
            continue
        for place, function in _functions(path, tree):
            if len(tokens := _tokens(function)) >= MIN_TOKENS:
                units.append(Unit(place, _shingles(tokens)))
    return units


def _similarity(left: frozenset[int], right: frozenset[int]) -> float:
    return len(left & right) / len(left | right)


def _pairs_of(units: list[Unit]) -> Iterator[ClonePair]:
    frequency: Counter[int] = Counter()
    for unit in units:
        frequency.update(unit.shingles)
    index: defaultdict[int, list[Unit]] = defaultdict(list)
    for unit in units:
        rarest_first = sorted(unit.shingles, key=lambda shingle: (frequency[shingle], shingle))
        indexed = len(rarest_first) - math.ceil(SIMILARITY_THRESHOLD * len(rarest_first)) + 1
        candidates: set[Unit] = set()
        for shingle in rarest_first[:indexed]:
            candidates.update(index[shingle])
            index[shingle].append(unit)
        for other in candidates:
            if other.place.path != unit.place.path and _similarity(unit.shingles, other.shingles) >= SIMILARITY_THRESHOLD:
                yield ClonePair.of(unit.place, other.place)


def find_pairs(sources: dict[str, bytes]) -> frozenset[ClonePair]:
    """The cross-file pairs among the functions of the given Python sources; a file Python cannot parse has none."""
    return frozenset(_pairs_of(_units(sources)))


def summarise_pairs(pairs: frozenset[ClonePair]) -> dict[Area, int]:
    """A pair counts as tests only when both of its files are; a test that copies production code is production."""
    tests = sum(ratchet.area_of(pair.first.path) is Area.TESTS and ratchet.area_of(pair.second.path) is Area.TESTS for pair in pairs)
    return {Area.PRODUCTION: len(pairs) - tests, Area.TESTS: tests}


def report_line(pairs: frozenset[ClonePair]) -> str:
    counts = ", ".join(f"{area.value}: {count} pair(s)" for area, count in summarise_pairs(pairs).items())
    return f"{RULE}: report only (no [clones] in {ratchet.BASELINE_PATH}) - {counts}"
