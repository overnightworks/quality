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

from quality import ratchet
from quality.ratchet import Area, ClonePair, Place
from quality.report import Finding
from quality.rules import Rule

MIN_TOKENS = 40
SHINGLE_SIZE = 5
SIMILARITY_THRESHOLD = 0.9
IDENTIFIER_PLACEHOLDER = "ID"
KEYWORD_SPREAD_PLACEHOLDER = "**"
FUNCTION_NODES = ast.FunctionDef | ast.AsyncFunctionDef


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


def _emit(node: ast.AST, tokens: list[str]) -> None:
    if isinstance(node, ast.expr_context):
        return
    tokens.append(type(node).__name__)
    if isinstance(node, ast.Constant):
        tokens.append(_literal_class(node.value))
    elif isinstance(node, ast.Name | ast.Attribute | ast.arg):
        tokens.append(IDENTIFIER_PLACEHOLDER)
    elif isinstance(node, ast.keyword):
        tokens.append(IDENTIFIER_PLACEHOLDER if node.arg else KEYWORD_SPREAD_PLACEHOLDER)
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
                units.append(Unit(place, function.lineno, _shingles(tokens)))
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


def report_line(pairs: frozenset[ClonePair]) -> str:
    counts = ", ".join(f"{area.value}: {count} pair(s)" for area, count in summarise_pairs(pairs).items())
    return f"{Rule.CLONE}: report only (no [clones] in {ratchet.BASELINE_PATH}) - {counts}"


def compare_pairs(measured: ClonePairs, held: frozenset[ClonePair]) -> list[Finding]:
    """The enforced rule: a pair the tree has and the baseline lacks is new, one the baseline holds and the tree
    lacks is stale, and a stale pair is red even when a side was deleted. Findings follow the pairs' own order."""
    findings = {pair: _new_pair_finding(pair, measured.lines[pair.second]) for pair in measured.pairs - held}
    findings |= {pair: _stale_pair_finding(pair) for pair in held - measured.pairs}
    return [findings[pair] for pair in sorted(findings)]


def _new_pair_finding(pair: ClonePair, line: int) -> Finding:
    reason = f"function {pair.second.symbol} is a near copy of {pair.first.path} {pair.first.symbol} (new pair)."
    fix = "give the shared body one owner and call it, or make the cases one parametrized test"
    return Finding(Rule.CLONE, pair.second.path, line, reason, fix)


def _stale_pair_finding(pair: ClonePair) -> Finding:
    reason = f"the baseline holds the pair {pair.label}, which is gone (stale)."
    return Finding(Rule.CLONE, pair.first.path, None, reason, f"lower it with `{ratchet.WRITE_COMMAND}`")


def check_clones(tree: ratchet.TreeMeasurement, sources: dict[str, bytes], baseline: ratchet.Baseline | None) -> ratchet.RuleResult:
    """Report-only without a `[clones]` table, even in a baseline of size tables; with one, every new pair and every
    held pair the tree lost is a finding. A pair can hide in a file the detector cannot read, so then it cannot judge."""
    pairs = find_pairs(sources)
    if baseline is None or baseline.clones is None:
        return ratchet.RuleResult([], report_line(pairs.pairs))
    ratchet.complete(tree)
    return ratchet.RuleResult(compare_pairs(pairs, baseline.clones), None)
