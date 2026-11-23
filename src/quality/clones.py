"""The `clone` rule: functions in two different Python files whose structure is nearly the same.

A function is its token stream: the arguments and the body without its docstring, walked in pre-order, with every
name, attribute, argument and keyword name folded to `ID` and every literal folded to its class, so a copy that only
renamed things still matches. Two functions are a pair when the Jaccard similarity of their 5-token shingles is at
least 0.9; only each function's rarest shingles are indexed, since a pair at that threshold must share one.
"""

import ast
import math
from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TypeGuard

from quality import baseline_file, ratchet
from quality.baseline_file import ClonePair, Place
from quality.ratchet import Area, Language, language_of
from quality.report import Finding, FindingEvent, RuleResult
from quality.rules import Rule

MIN_TOKENS = 40
SHINGLE_SIZE = 5
SIMILARITY_THRESHOLD = 0.9
IDENTIFIER_PLACEHOLDER = "ID"
KEYWORD_SPREAD_PLACEHOLDER = "**"
FUNCTION_NODES = ast.FunctionDef | ast.AsyncFunctionDef
TEST_NAME_PREFIX = "test"
WRITTEN_NAME_FIELD = {ast.Name: "id", ast.Attribute: "attr", ast.arg: "arg", ast.keyword: "arg"}


@dataclass(frozen=True)
class Unit:
    """A function long enough to be a copy, with the shingles of its token stream."""

    place: Place
    line: int
    shingles: frozenset[int]


@dataclass(frozen=True)
class ClonePairs:
    """The pairs of a tree, and the line of every place in them; the line is kept apart because a pair's equality is
    the baseline's identity."""

    pairs: frozenset[ClonePair]
    lines: Mapping[Place, int]


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


def _name_tokens(node: ast.AST, names_kept: bool) -> list[str]:
    """The name an identifier node carries as written or folded; a nested `def` or `class` carries its name only as written."""
    if isinstance(node, ast.keyword) and node.arg is None:
        return [KEYWORD_SPREAD_PLACEHOLDER]
    if field := WRITTEN_NAME_FIELD.get(type(node)):
        return [getattr(node, field) if names_kept else IDENTIFIER_PLACEHOLDER]
    return [node.name] if names_kept and isinstance(node, FUNCTION_NODES | ast.ClassDef) else []


def _emit(node: ast.AST, tokens: list[str], names_kept: bool, literals_kept: bool) -> None:
    if isinstance(node, ast.expr_context):
        return
    tokens.append(type(node).__name__)
    if isinstance(node, ast.Constant):
        tokens.append(repr(node.value) if literals_kept else _literal_class(node.value))
    tokens.extend(_name_tokens(node, names_kept))
    for child in ast.iter_child_nodes(node):
        _emit(child, tokens, names_kept, literals_kept)


def is_test_function(node: ast.AST) -> TypeGuard[FUNCTION_NODES]:
    """Whether `node` is a function whose name starts a test, in a class or not."""
    return isinstance(node, FUNCTION_NODES) and node.name.startswith(TEST_NAME_PREFIX)


def _has_docstring(body: list[ast.stmt]) -> bool:
    return (
        bool(body)
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    )


def tokens(function: FUNCTION_NODES, names_kept: bool, literals_kept: bool) -> list[str]:
    """The token stream of a function's arguments and its body without the docstring, in pre-order. Names are folded to
    `ID` unless `names_kept` (a nested `def` or `class` then also emits its name), literals to their class unless
    `literals_kept` (then as their `repr`); the clone rule keeps neither."""
    stream: list[str] = []
    _emit(function.args, stream, names_kept, literals_kept)
    for statement in function.body[1:] if _has_docstring(function.body) else function.body:
        _emit(statement, stream, names_kept, literals_kept)
    return stream


def _shingles(stream: list[str]) -> frozenset[int]:
    return frozenset(hash(tuple(stream[start : start + SHINGLE_SIZE])) for start in range(len(stream) - SHINGLE_SIZE + 1))


def _functions(path: str, tree: ast.Module) -> Iterator[tuple[Place, FUNCTION_NODES]]:
    """Every function in source order, named the way the size rule names it."""
    symbols = ratchet.qualified_symbols(tree)
    occurrences: Counter[str] = Counter()
    for node in sorted((node for node in ast.walk(tree) if isinstance(node, FUNCTION_NODES)), key=lambda node: node.lineno):
        qualified = symbols[node.lineno]
        occurrences[qualified] += 1
        yield Place(path, ratchet.nth(qualified, occurrences[qualified])), node


def parsed(path: str, source: bytes) -> ast.Module | None:
    try:
        return ast.parse(ratchet.python_text(source), filename=path)
    except ratchet.PARSE_ERRORS:
        return None


def python_test_trees(sources: dict[str, bytes]) -> Iterator[tuple[str, ast.Module]]:
    """The parsed Python test files among `sources`; a file Python cannot parse is left out."""
    for path, source in sources.items():
        is_python_test = language_of(path) is Language.PYTHON and Language.PYTHON.is_test_file(PurePosixPath(path))
        if is_python_test and (tree := parsed(path, source)) is not None:
            yield path, tree


def _units(sources: dict[str, bytes]) -> list[Unit]:
    units = []
    for path, source in sources.items():
        if (tree := parsed(path, source)) is None:
            continue
        for place, function in _functions(path, tree):
            if len(stream := tokens(function, names_kept=False, literals_kept=False)) >= MIN_TOKENS:
                units.append(Unit(place, function.lineno, _shingles(stream)))
    return units


def _similarity(left: frozenset[int], right: frozenset[int]) -> float:
    return len(left & right) / len(left | right)


def _pairs_of(units: list[Unit]) -> Iterator[tuple[Unit, Unit]]:
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
                yield unit, other


def find_pairs(sources: dict[str, bytes]) -> ClonePairs:
    """The cross-file pairs among the functions of the Python files among the given sources; a file Python cannot
    parse has none."""
    python = {path: source for path, source in sources.items() if ratchet.language_of(path) is ratchet.Language.PYTHON}
    found = list(_pairs_of(_units(python)))
    pairs = frozenset(ClonePair.of(unit.place, other.place) for unit, other in found)
    return ClonePairs(pairs, {unit.place: unit.line for pair in found for unit in pair})


def summarise_pairs(pairs: frozenset[ClonePair]) -> dict[Area, int]:
    """A pair counts as tests only when both of its files are; a test that copies production code is production."""
    tests = sum(ratchet.area_of(pair.first.path) is Area.TESTS and ratchet.area_of(pair.second.path) is Area.TESTS for pair in pairs)
    return {Area.PRODUCTION: len(pairs) - tests, Area.TESTS: tests}


def compare_pairs(measured: ClonePairs, held: frozenset[ClonePair]) -> list[Finding]:
    """The enforced rule: a pair the tree has and the baseline lacks is new, one the baseline holds and the tree
    lacks is stale, and a stale pair is red even when a side was deleted. Findings follow the pairs' own order."""
    findings = {pair: _new_pair_finding(pair, measured.lines[pair.second]) for pair in measured.pairs - held}
    findings |= {pair: _stale_pair_finding(pair) for pair in held - measured.pairs}
    return [findings[pair] for pair in sorted(findings)]


def _new_pair_finding(pair: ClonePair, line: int) -> Finding:
    fact = f"function {pair.second.symbol} and {pair.first.path} {pair.first.symbol}"
    fix = "give the shared body one owner and call it, or make the cases one parametrized test"
    return Finding(Rule.CLONE, pair.second.path, line, fact, fix, event=FindingEvent.NEAR_COPY)


def _stale_pair_finding(pair: ClonePair) -> Finding:
    fact = f"pair {pair.label} is gone"
    return Finding(Rule.CLONE, pair.first.path, None, fact, baseline_file.FIX_COMMAND, event=FindingEvent.CAN_FALL)


def check_clones(tree: ratchet.TreeMeasurement, sources: dict[str, bytes], baseline: baseline_file.Baseline | None) -> RuleResult:
    """Report-only without a `[clones]` table, even in a baseline of size tables; with one, every new pair and every
    held pair the tree lost is a finding. A pair can hide in a file the detector cannot read, so then it cannot judge."""
    pairs = find_pairs(sources)
    if baseline is None or baseline.clones is None:
        return RuleResult([], len(pairs.pairs), True)
    ratchet.complete(tree)
    return RuleResult(compare_pairs(pairs, baseline.clones), len(baseline.clones), False)
